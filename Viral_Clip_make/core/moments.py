from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from .util import Logger, clamp

# High-arousal / "power" words that tend to keep viewers watching.
POWER_WORDS = {
    "amazing", "insane", "crazy", "unbelievable", "incredible", "shocking", "wild",
    "best", "worst", "greatest", "perfect", "terrible", "awful", "huge", "massive",
    "secret", "truth", "lie", "exposed", "revealed", "nobody", "everybody", "everyone",
    "never", "always", "stop", "wait", "listen", "look", "watch", "remember",
    "first", "last", "only", "real", "actually", "literally",
    "win", "won", "lose", "lost", "fail", "failed", "success", "sorry", "wrong", "right",
    "why", "how", "what", "when", "where", "who", "because", "until", "finally",
    "guaranteed", "free", "easy", "hard", "fast", "slow", "now", "today",
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

# Story/leak recap language. A cold viewer cannot enjoy a plot summary, and
# pre-release ending leaks get taken down. These are hard rejects for a pick.
SPOILER_RE = re.compile(
    r"\b(?:spoilers?|leak(?:ed|s)?|endings?|epilogue|finale|final mission|"
    r"happily ever after|sacrifice|dies|death of|true ending|secret ending|"
    r"story order|chapter\s*\d+|director|ign|interview|plot|lore|canon|cutscene|"
    r"credits|story complete|two years later|2 years later|every decision|choices? made)\b",
    re.I,
)

# An opening made only of fillers/interjections names nothing a stranger can
# care about - there is no premise before the reaction.
GENERIC_ONLY_RE = re.compile(
    r"^(?:(?:and|but|so|yeah|yes|ok|okay|um|uh|well|like|you know|i mean|oh|wow|"
    r"bro|yo|look|watch|wait|listen|this|that|it|they|he|she|there|here|what|"
    r"how|why|no way|oh my god|oh my gosh)[\s,.!?\-]*)+$",
    re.I,
)
BAD_START_RE = re.compile(r"^(?:and|but|so|he|she|it|they|that|this|then|or)\b", re.I)
EXCITE_RE = re.compile(
    r"\b(?:no way|oh my (?:god|gosh)|wtf|omg|holy|insane|crazy|unbelievable|"
    r"shocking|hilarious|lmao|haha+|lol|finally|died|crash(?:ed)?|explosion|"
    r"arrest(?:ed)?|chase)\b|!|\d",
    re.I,
)

# Shorts lose viewers past ~35 s. Even when the UI asks for a longer window,
# the picker keeps a single beat short enough to finish.
RETENTION_MIN_CAP = 22.0
RETENTION_MAX_CAP = 35.0

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


def window_limits(cfg, log: Logger | None = None) -> tuple[float, float]:
    """Effective clip window, capped for Shorts retention."""
    min_len = float(cfg.get("general.min_seconds", 12))
    max_len = float(cfg.get("general.max_seconds", 45))
    min_len = max(8.0, min_len)
    max_len = max(min_len + 5.0, max_len)
    eff_min = min(min_len, RETENTION_MIN_CAP)
    eff_max = min(max_len, RETENTION_MAX_CAP)
    eff_max = max(eff_min, eff_max)
    if log and (eff_min < min_len or eff_max < max_len):
        log.info(
            "Retention guard: using %.0f-%.0fs windows instead of %.0f-%.0fs "
            "(Shorts lose viewers on long setups)",
            eff_min, eff_max, min_len, max_len,
        )
    return eff_min, eff_max


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
    return max(sentences, key=lambda s: (sum(_keyword_hits(s)), min(len(s), 90)))


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
    """Reward windows that begin/end on silence, not on a mid-sentence cut."""
    if not rms:
        return 0.5
    idx = clamp(int(time / hop), 0, len(rms) - 1)
    band = rms[max(0, idx - 4):idx + 1] if at_start else rms[idx:min(len(rms), idx + 5)]
    if not band:
        return 0.5
    quietest = min(band)
    # A real silence boundary is AT or BELOW the silence threshold. The old
    # code rewarded levels *equal* to the threshold, so ordinary quiet speech
    # scored better than actual silence.
    if quietest <= threshold:
        return 1.0
    return clamp(1.0 - (quietest - threshold) / max(threshold * 3, 1e-6), 0, 1)


def spoiler_source(transcript: dict) -> tuple[bool, dict]:
    """Detect a spoilery explainer/leak source before picking any windows.

    A lore video mentions endings, chapters and the director dozens of times.
    Cutting it into Shorts produces spoiler recaps nobody can enjoy cold, so
    the honest answer is no clips, not ten weak ones.
    """
    text = transcript.get("text") or " ".join(
        str(s.get("text", "")) for s in (transcript.get("segments") or [])
    )
    counts = Counter(m.group(0).lower() for m in SPOILER_RE.finditer(text))
    total = sum(counts.values())
    return (total >= 8 or len(counts) >= 4), dict(counts)


def score_signals(transcript: dict, rms: list[float], start: float, end: float,
                  cfg=None, duration: float | None = None) -> dict:
    """Measured signals for one window - used by both local and AI picks."""
    words = transcript.get("words") or []
    duration = float(duration if duration is not None else
                     (transcript.get("duration") or (words[-1]["end"] if words else 0)))
    length = max(0.1, end - start)
    in_words = [w for w in words if start <= float(w["start"]) < end]
    text = _text_of(in_words)
    wps = len(in_words) / length
    power, curiosity, emotion = _keyword_hits(text)
    hits = power + curiosity + emotion

    hop = 0.25
    global_mean = (sum(rms) / len(rms)) if rms else 0.01
    if global_mean <= 1e-5:
        global_mean = 0.01
    silence_threshold = global_mean * 0.18
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

    opening = " ".join(str(w["text"]) for w in in_words[:12])
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
    return {
        "score": clamp(score, 0, 100),
        "signals": signals,
        "text": text,
        "opening": opening,
        "words": in_words,
        "silence": silence,
        "length": length,
    }


def quality_flags(transcript: dict, start: float, end: float, title: str = "",
                  hook: str = "", reason: str = "", ai_flags: dict | None = None) -> dict:
    """Check the rules a stranger applies before sharing a clip.

    The AI is asked to answer the subjective ones (premise / payoff / picture /
    standalone). Local text checks catch the objective failures - especially
    spoiler recaps - and also work when there is no API configured.
    """
    ai_flags = ai_flags or {}
    sig = score_signals(transcript, [], start, end)
    opening = (ai_flags.get("opening") or sig["opening"] or "").strip()
    length = max(0.1, end - start)

    meta = " ".join(str(x) for x in (title, hook, reason))
    meta_spoiler = bool(SPOILER_RE.search(meta))
    text_terms = {m.group(0).lower() for m in SPOILER_RE.finditer(sig["text"])}
    text_spoiler = len(text_terms) >= 2

    premise = ai_flags.get("premise")
    if premise is None:
        premise = bool(re.search(r"\d", opening)) or bool(
            re.search(r"\b[A-Z][a-z]{2,}\b", opening[1:])
        ) or (bool(opening) and not bool(GENERIC_ONLY_RE.fullmatch(opening)))

    payoff_late = ai_flags.get("payoff_in_last_40pct")
    if payoff_late is None:
        segments = [s for s in (transcript.get("segments") or [])
                    if float(s["end"]) > start and float(s["start"]) < end]
        if segments:
            peak = max(segments, key=lambda s: len(EXCITE_RE.findall(str(s.get("text", "")))))
            payoff_late = float(peak["start"]) - start >= 0.6 * length
        else:
            payoff_late = True  # unknown: don't guess without evidence

    standalone = ai_flags.get("standalone")
    if standalone is None:
        standalone = not bool(BAD_START_RE.match(opening))

    visual_payoff = ai_flags.get("visual_payoff", True)
    picture_first = ai_flags.get("picture_first", True)

    flags = {
        "spoiler": bool(ai_flags.get("spoiler")) or meta_spoiler or text_spoiler,
        "premise": bool(premise),
        "payoff_late": bool(payoff_late),
        "standalone": bool(standalone),
        "visual_payoff": visual_payoff is not False,
        "picture_first": picture_first is not False,
    }
    # "spoiler" is the one inverted flag: True is the failure, False is good.
    failed = []
    for key, ok in flags.items():
        if (key == "spoiler" and ok) or (key != "spoiler" and ok is False):
            failed.append(key)
    flags["failed"] = failed
    return flags


def score_window(transcript: dict, rms: list[float], start: float, end: float,
                 cfg, duration: float | None = None, title: str = "", hook: str = "",
                 reason: str = "", ai_flags: dict | None = None) -> tuple[float, dict, dict]:
    """Score + quality-check an arbitrary window (local picks and AI picks alike)."""
    sig = score_signals(transcript, rms, start, end, cfg, duration)
    flags = quality_flags(transcript, start, end, title, hook, reason, ai_flags)
    score = sig["score"]

    if flags["spoiler"]:
        score = 0.0
    else:
        if not flags["premise"]:
            score -= 22.0
        if not flags["payoff_late"]:
            score -= 16.0
        if not flags["standalone"]:
            score -= 12.0
        if not flags["visual_payoff"]:
            score -= 12.0
        if not flags["picture_first"]:
            score -= 10.0
        max_len = min(float(cfg.get("general.max_seconds", 45)), RETENTION_MAX_CAP)
        if sig["length"] > max_len:
            score -= (sig["length"] - max_len) * 1.5
    return clamp(score, 0, 100), flags, sig


def find_moments(transcript: dict, rms: list[float], cfg, log: Logger) -> list[Moment]:
    """Pick the most engaging windows using only local signals - no AI API, no cost."""
    words = transcript.get("words") or []
    segments = transcript.get("segments") or []
    duration = float(transcript.get("duration") or (words[-1]["end"] if words else 0))
    min_len, max_len = window_limits(cfg, log)
    wanted = int(cfg.get("general.clips_per_video", 5))

    is_spoiler, terms = spoiler_source(transcript)
    if is_spoiler:
        log.warn("Source is a spoiler/recap explainer (%s) - no clips picked",
                 ", ".join(f"{k} x{v}" for k, v in list(terms.items())[:5]))
        return []

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
        measured, flags, sig = score_window(transcript, rms, start, end, cfg, duration)
        if flags["spoiler"] or not flags["premise"] or not flags["standalone"]:
            continue

        in_words = sig["words"]
        # The hook should name what actually happens at the end, not a random
        # keyword-rich line from the middle of the setup.
        tail_start = start + 0.6 * (end - start)
        tail = " ".join(str(w["text"]) for w in in_words if float(w["start"]) >= tail_start)
        line = _best_line(tail) or _best_line(sig["text"]) or sig["text"]
        hook = _shorten(line.upper(), 46)
        title = _shorten(line.title(), 62) or f"Viral moment at {int(start)}s"

        signals = list(sig["signals"])
        if not flags["payoff_late"]:
            signals.append("payoff early")
        reason = ", ".join(signals) if signals else "local signal score"

        scored.append(Moment(round(start, 2), round(end, 2), round(measured, 1),
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
