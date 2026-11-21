from __future__ import annotations

import re
from dataclasses import dataclass

from .util import Logger, clamp

# High-arousal / "power" words that tend to keep viewers watching.
POWER_WORDS = {
    "amazing", "insane", "crazy", "unbelievable", "incredible", "shocking", "wild",
    "best", "worst", "greatest", "perfect", "terrible", "awful", "huge", "massive",
    "secret", "truth", "lie", "exposed", "revealed", "nobody", "everybody", "everyone",
    "never", "always", "stop", "wait", "listen", "look", "watch", "remember",
    "first", "last", "only", "real", "actually", "literally", "literally", "literally",
    "win", "won", "lose", "lost", "fail", "failed", "success", "sorry", "wrong", "right",
    "why", "how", "what", "when", "where", "who", "because", "until", "finally",
    "garanteed", "guaranteed", "free", "easy", "hard", "fast", "slow", "now", "today",
}

CURIOSITY_WORDS = {
    "what", "why", "how", "when", "where", "who", "until", "before", "after", "because",
    "secret", "reason", "truth", "happen", "happens", "next", "result", "outcome",
    "turns", "turned", "found", "discovered", "realize", "realised", "noticed",
}

EMOTION_WORDS = {
    "love", "hate", "angry", "happy", "sad", "scared", "afraid", "excited", "nervous",
    "shocked", "surprised", "disgusting", "beautiful", "gross", "funny", "hilarious",
    "pain", "hurt", "fun", "enjoy", "adore", "miss", "proud", "embarrassed",
}

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Moment:
    start: float
    end: float
    score: float
    title: str
    hook: str
    reason: str
    words: int = 0


def _text_of(words: list[dict]) -> str:
    return " ".join(w["text"].strip() for w in words if w.get("text", "").strip())


def _keyword_hits(text: str) -> tuple[int, int, int]:
    tokens = re.findall(r"[a-z']+", text.lower())
    power = sum(1 for t in tokens if t in POWER_WORDS)
    curiosity = sum(1 for t in tokens if t in CURIOSITY_WORDS)
    emotion = sum(1 for t in tokens if t in EMOTION_WORDS)
    return power, curiosity, emotion


def _best_line(text: str) -> str:
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    if not sentences:
        return text.strip()
    best = max(sentences, key=lambda s: (sum(_keyword_hits(s)), min(len(s), 90)))
    return best


def _shorten(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if " " in cut:
        cut = cut[:cut.rfind(" ")]
    return cut.rstrip(",;:- ") + "..."


def _silence_fraction(rms: list[float], start: float, end: float, hop: float, threshold: float) -> float:
    if not rms:
        return 0.0
    i0, i1 = int(start / hop), int(end / hop)
    segment = rms[max(0, i0):max(0, i1)]
    if not segment:
        return 0.0
    return sum(1 for v in segment if v < threshold) / len(segment)


def _boundary_bonus(rms: list[float], time: float, hop: float, threshold: float,
                    duration: float, at_start: bool) -> float:
    """Reward windows that begin/end on a natural silence boundary."""
    if not rms:
        return 0.5
    idx = int(time / hop)
    idx = clamp(idx, 0, len(rms) - 1)
    if at_start:
        lookback = rms[max(0, idx - 4):idx + 1]
        if not lookback:
            return 0.5
        best = min(lookback)
        return clamp(1.0 - abs(best - threshold) / max(threshold, 1e-6), 0, 1)
    lookahead = rms[idx:min(len(rms), idx + 5)]
    if not lookahead:
        return 0.5
    best = min(lookahead)
    return clamp(1.0 - abs(best - threshold) / max(threshold, 1e-6), 0, 1)


def find_moments(transcript: dict, rms: list[float], cfg, log: Logger) -> list[Moment]:
    """Pick the most engaging windows using only local signals - no AI API, no cost."""
    words = transcript.get("words") or []
    segments = transcript.get("segments") or []
    duration = float(transcript.get("duration") or (words[-1]["end"] if words else 0))
    min_len = float(cfg.get("general.min_seconds", 12))
    max_len = float(cfg.get("general.max_seconds", 45))
    wanted = int(cfg.get("general.clips_per_video", 5))
    hop = 0.25

    if min_len < 8:
        min_len = 8
    if max_len > 60:
        max_len = 60
    if max_len < min_len:
        max_len = min_len + 5

    global_mean = (sum(rms) / len(rms)) if rms else 0.01
    if global_mean <= 1e-5:
        global_mean = 0.01
    silence_threshold = global_mean * 0.18

    # ---- build candidate windows from transcript segments -----------------
    candidates: list[tuple[float, float]] = []
    if segments:
        for i, seg in enumerate(segments):
            start = float(seg["start"])
            for j in range(i, len(segments)):
                end = float(segments[j]["end"])
                length = end - start
                if length < min_len:
                    continue
                if length > max_len:
                    break
                if start + length <= duration + 0.5:
                    candidates.append((start, end))
    if not candidates and duration >= min_len:
        # Silent / untranscribed fallback: step through on energy only.
        cursor = 0.0
        while cursor + max_len <= duration:
            candidates.append((cursor, cursor + max_len))
            cursor += max_len * 0.6
        if not candidates:
            candidates = [(0.0, min(duration, max_len))]

    # Cap the search space so long videos stay fast.
    max_candidates = int(cfg.get("speed.candidates", 600))
    if len(candidates) > max_candidates:
        step = len(candidates) / max_candidates
        candidates = [candidates[int(i * step)] for i in range(max_candidates)]

    scored: list[Moment] = []
    for start, end in candidates:
        length = max(0.1, end - start)
        in_words = [w for w in words if start <= float(w["start"]) < end]
        text = _text_of(in_words)
        wps = len(in_words) / length
        power, curiosity, emotion = _keyword_hits(text)
        hits = power + curiosity + emotion

        if rms:
            i0, i1 = int(start / hop), int(end / hop)
            band = rms[max(0, i0):max(0, i1)]
            mean_energy = (sum(band) / len(band)) if band else global_mean
        else:
            mean_energy = global_mean
        energy_norm = clamp(mean_energy / (global_mean * 1.35), 0, 1)
        silence = _silence_fraction(rms, start, end, hop, silence_threshold)

        density_norm = clamp((wps - 0.9) / 2.6, 0, 1)
        keyword_norm = clamp(hits / 6.0, 0, 1)

        opening = " ".join(w["text"] for w in in_words[:12])
        has_question = "?" in opening
        has_exclaim = "!" in opening
        has_number = bool(re.search(r"\d", opening))
        op_power, op_curiosity, _ = _keyword_hits(opening)
        hook_norm = 1.0 if (has_question or has_exclaim or has_number or
                            op_power >= 2 or op_curiosity >= 2) else 0.25

        punct = text.count(".") + text.count("!") + text.count("?")
        punct_norm = clamp(punct / 3.0, 0, 1)

        start_c = _boundary_bonus(rms, start, hop, silence_threshold, duration, True)
        end_c = _boundary_bonus(rms, end, hop, silence_threshold, duration, False)
        complete_norm = (start_c + end_c) / 2.0

        score = (
            0.20 * density_norm +
            0.16 * energy_norm +
            0.20 * keyword_norm +
            0.16 * hook_norm +
            0.14 * complete_norm +
            0.10 * punct_norm
        ) * 100.0
        score -= 16.0 * silence
        score = clamp(score, 0, 100)

        line = _best_line(text) or text
        hook = _shorten(line.upper(), 46)
        title = _shorten(line.title(), 62) or f"Viral moment at {int(start)}s"

        signals = []
        if density_norm > 0.6:
            signals.append("fast speech")
        if energy_norm > 0.7:
            signals.append("high energy")
        if hook_norm > 0.8:
            signals.append("strong hook")
        if keyword_norm > 0.5:
            signals.append("emotional words")
        if complete_norm > 0.7:
            signals.append("clean edges")
        reason = ", ".join(signals) if signals else "local signal score"

        scored.append(Moment(round(start, 2), round(end, 2), round(score, 1),
                             title, hook, reason, len(in_words)))

    if not scored:
        log.warn("No candidate moments found in this video")
        return []

    # ---- non-maximum suppression: keep the best, drop overlapping windows --
    scored.sort(key=lambda m: m.score, reverse=True)
    picked: list[Moment] = []
    for moment in scored:
        too_close = False
        for chosen in picked:
            overlap = min(moment.end, chosen.end) - max(moment.start, chosen.start)
            if overlap <= 0:
                continue
            shorter = min(moment.end - moment.start, chosen.end - chosen.start)
            if shorter > 0 and overlap / shorter >= 0.40:
                too_close = True
                break
        if not too_close:
            picked.append(moment)
        if len(picked) >= wanted:
            break

    # Strongest moment first: clip 01 is the one people are most likely to share.
    picked.sort(key=lambda m: m.score, reverse=True)
    log.ok("Selected %d local viral moments (top score %.1f/100)",
           len(picked), picked[0].score if picked else 0)
    return picked
