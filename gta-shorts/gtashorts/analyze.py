from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .ffmpeg import MediaInfo
from .util import LOG, clamp, retry

EMOTION_WORDS = {
    "insane", "crazy", "unbelievable", "wtf", "no way", "omg", "shocking", "epic",
    "fail", "funny", "hilarious", "police", "chase", "explosion", "clutch", "best",
    "worst", "record", "secret", "hidden", "trap", "rage", "speedrun", "glitch",
    "caught", "busted", "arrest", "fight", "boss", "heist", "money", "rich",
}

SYSTEM_PROMPT = """You are a YouTube Shorts editor who has produced hundreds of millions of views.
You pick short moments from long gaming videos that will stop the scroll in the first second.
You are ruthless: a boring moment that is technically fine is a rejection."""

PROMPT = """Here is the transcript of a gameplay video titled: "{title}"
Source duration: {duration:.0f} seconds. Language: {language}.

TRANSCRIPT (segments, seconds):
{transcript}

Pick the {count} best moments for vertical YouTube Shorts.

Hard rules for every moment:
1. Duration {min_dur}-{max_dur} seconds. Aim for {target_min}-{target_max} seconds.
2. Start exactly where the action/sentence starts - no wind-up, no "so anyway". The first 0.5s must already be interesting.
3. End on the payoff or the funniest/biggest beat, not after it.
4. Moments must not overlap each other.
5. Only use timestamps that exist in the transcript and do not invent events.
6. Skip anything that needs missing context, is mostly silence, or is offensive/hateful/dangerous.

Ranking rubric (0-10 each, be honest and use the full range):
- hook: does second one create a question or shock?
- emotion: laughter, tension, surprise, awe?
- payoff: is there a satisfying end?
- clarity: does it make sense with zero context?
- loop: can the last frame lead straight back into the first?
- safety: advertiser-friendly, no copyrighted music claims, no real people harmed.

Write metadata:
- title: max 70 characters, curiosity + keyword, no clickbait lies, no ALL CAPS except 1-2 words.
- hook: max 5 words, ALL CAPS, burned on screen for the first ~2.8s. This is the text that stops the scroll.
- description: 1-2 short sentences + 4-5 hashtags. End with #Shorts.
- hashtags: exactly 5, each starting with #, lowercase, relevant (game name, topic, #shorts).
- reason: one sentence explaining why it can go viral.

Return ONLY valid JSON with this shape:
{{"moments":[{{"start":0.0,"end":0.0,"title":"","hook":"","description":"","hashtags":["#a","#b","#c","#d","#e"],"scores":{{"hook":0,"emotion":0,"payoff":0,"clarity":0,"loop":0,"safety":0}},"reason":""}}]}}"""


@dataclass
class Moment:
    start: float
    end: float
    title: str = ""
    hook: str = ""
    description: str = ""
    hashtags: list[str] = field(default_factory=list)
    scores: dict = field(default_factory=dict)
    reason: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def ai_score(self) -> float:
        weights = {"hook": 0.26, "emotion": 0.20, "payoff": 0.20, "clarity": 0.14, "loop": 0.10, "safety": 0.10}
        total = 0.0
        for key, weight in weights.items():
            try:
                total += clamp(float(self.scores.get(key, 5)), 0, 10) * weight
            except (TypeError, ValueError):
                total += 5 * weight
        return round(total * 10, 1)


def _transcript_text(segments: list[dict], limit: int = 60_000) -> str:
    lines = []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        lines.append(f"[{seg['start']:.1f}-{seg['end']:.1f}] {text}")
    out = "\n".join(lines)
    if len(out) > limit:
        out = out[:limit] + "\n...[transcript truncated]"
    return out


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text)
        text = re.sub(r"```$", "", text).strip()
    first, last = text.find("{"), text.rfind("}")
    if first == -1 or last == -1:
        raise ValueError("no JSON object in Gemini response")
    return json.loads(text[first : last + 1])


def _client(cfg):
    from google import genai

    key = cfg.gemini_key()
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY is missing. Get a free key at https://aistudio.google.com/apikey "
            "and put it in .env as GEMINI_API_KEY=..."
        )
    return genai.Client(api_key=key)


def select_moments(info: MediaInfo, transcript: dict, cfg, title: str = "", stub: bool = False) -> list[Moment]:
    if stub:
        return _stub_moments(info, transcript, cfg)
    if not cfg.gemini_key():
        raise RuntimeError(
            "GEMINI_API_KEY is missing. Get a free key at https://aistudio.google.com/apikey "
            "and put it in .env as GEMINI_API_KEY=..."
        )

    count = int(cfg.get("gemini.candidates", 12))
    min_dur = float(cfg.get("video.clip_min_seconds", 15))
    max_dur = float(cfg.get("video.clip_max_seconds", 55))
    target_min = float(cfg.get("video.target_min_seconds", 22))
    target_max = float(cfg.get("video.target_max_seconds", 38))

    prompt = PROMPT.format(
        title=title or "untitled gameplay",
        duration=info.duration,
        language=transcript.get("language") or "en",
        transcript=_transcript_text(transcript.get("segments") or []),
        count=count,
        min_dur=int(min_dur),
        max_dur=int(max_dur),
        target_min=int(target_min),
        target_max=int(target_max),
    )

    raw = None
    attempts = int(cfg.get("gemini.max_retries", 2)) + 1
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            raw = _ask_gemini(prompt, cfg)
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            LOG.warning("Gemini attempt %d/%d failed: %s", attempt, attempts, str(exc)[:300])
    if raw is None:
        LOG.error("Gemini could not analyse the video (%s). Using the offline fallback selector.", last_error)
        return _fallback_moments(info, transcript, cfg)

    moments = _parse_moments(raw, info, transcript, cfg)
    if not moments:
        LOG.warning("Gemini returned no usable moments - using the offline fallback selector.")
        return _fallback_moments(info, transcript, cfg)
    LOG.info("Gemini proposed %d usable moments", len(moments))
    return moments


def _ask_gemini(prompt: str, cfg) -> str:
    from google.genai import types

    client = _client(cfg)
    model = cfg.get("gemini.model", "gemini-2.5-flash")
    contents: list = [prompt]
    uploaded = None
    if cfg.get("gemini.use_video_input"):
        raise RuntimeError("use_video_input is not wired for this build; set it to false")

    def call():
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.75,
                response_mime_type="application/json",
            ),
        )
        return response.text or ""

    return retry(call, attempts=2, delay=3.0, what="Gemini request")


def _parse_moments(raw: str, info: MediaInfo, transcript: dict, cfg) -> list[Moment]:
    try:
        data = _extract_json(raw)
    except Exception as exc:  # noqa: BLE001
        LOG.error("could not parse Gemini JSON: %s", exc)
        LOG.debug("raw response: %s", (raw or "")[:2000])
        return []

    words = transcript.get("words") or []
    min_dur = float(cfg.get("video.clip_min_seconds", 15))
    max_dur = min(float(cfg.get("video.clip_max_seconds", 55)), info.duration)
    moments: list[Moment] = []
    for item in data.get("moments") or []:
        try:
            start = float(item.get("start"))
            end = float(item.get("end"))
        except (TypeError, ValueError):
            continue
        start, end = _snap(start, end, words, info.duration, min_dur, max_dur)
        if start is None:
            continue
        moment = Moment(
            start=start,
            end=end,
            title=_clean_title(item.get("title", "")),
            hook=_clean_hook(item.get("hook", "")),
            description=_clean_description(item.get("description", "")),
            hashtags=_clean_hashtags(item.get("hashtags") or [], cfg),
            scores=item.get("scores") or {},
            reason=(item.get("reason") or "").strip(),
        )
        moments.append(moment)

    # de-overlap and drop near-duplicates
    moments.sort(key=lambda m: m.ai_score, reverse=True)
    kept: list[Moment] = []
    for moment in moments:
        if any(_overlap(moment, other) > 0.25 for other in kept):
            continue
        kept.append(moment)
        if len(kept) >= int(cfg.get("video.max_clips_per_video", 6)):
            break
    kept.sort(key=lambda m: m.start)
    return kept


def _snap(start: float, end: float, words: list[dict], total: float, min_dur: float, max_dur: float):
    start = clamp(start, 0, max(0, total - 1))
    end = clamp(end, 0, total)
    if words:
        # start on the first word that begins at/after (start - 0.6s)
        candidates = [w for w in words if start - 0.6 <= w["start"] <= start + 1.0]
        if candidates:
            start = candidates[0]["start"]
        candidates_end = [w for w in words if end - 1.2 <= w["end"] <= end + 0.8]
        if candidates_end:
            end = candidates_end[-1]["end"] + 0.15
    if end - start < min_dur:
        end = min(total, start + min_dur)
    if end - start > max_dur:
        end = start + max_dur
    if end - start < min_dur - 0.25:
        return None, None
    return round(start, 2), round(end, 2)


def _overlap(a: Moment, b: Moment) -> float:
    inter = max(0.0, min(a.end, b.end) - max(a.start, b.start))
    shortest = max(0.1, min(a.duration, b.duration))
    return inter / shortest


def _clean_title(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip().strip('"')
    if len(text) > 95:
        text = text[:92].rstrip() + "..."
    return text


def _clean_hook(text: str) -> str:
    text = re.sub(r"[\r\n]+", " ", (text or "")).strip().strip('"')
    text = text.upper()
    words = text.split()
    return " ".join(words[:5])


def _clean_description(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _clean_hashtags(tags: list, cfg) -> list[str]:
    cleaned: list[str] = []
    for tag in tags:
        tag = str(tag).strip()
        if not tag:
            continue
        tag = "#" + re.sub(r"[^0-9A-Za-z_]", "", tag.lstrip("#"))
        if len(tag) > 2 and tag.lower() not in [t.lower() for t in cleaned]:
            cleaned.append(tag)
    for fallback in cfg.get("youtube.default_tags", []) or []:
        tag = "#" + re.sub(r"[^0-9A-Za-z_]", "", str(fallback))
        if tag.lower() not in [t.lower() for t in cleaned]:
            cleaned.append(tag)
    return cleaned[:5]


def _stub_moments(info: MediaInfo, transcript: dict, cfg) -> list[Moment]:
    """Deterministic moments for the self-test (no API key needed)."""
    duration = max(20.0, info.duration)
    length = clamp(float(cfg.get("video.target_max_seconds", 38)), 15, min(55, duration))
    limit = int(cfg.get("video.max_clips_per_video", 6))
    out: list[Moment] = []
    t = min(1.0, max(0.0, duration * 0.02))
    index = 1
    while t + 15 <= duration and len(out) < limit:
        end = min(duration, t + length)
        out.append(
            Moment(
                start=round(t, 2),
                end=round(end, 2),
                title=f"Self-test clip {index}",
                hook="SELF TEST HOOK",
                description="Self-test render. #Shorts #gta6 #test #captions #vertical",
                hashtags=["#gta6", "#shorts", "#test", "#vertical", "#captions"],
                scores={"hook": 8, "emotion": 7, "payoff": 7, "clarity": 9, "loop": 6, "safety": 10},
                reason="Generated by the offline stub analyser.",
            )
        )
        t = end + 1.0
        index += 1
    return out


def _fallback_moments(info: MediaInfo, transcript: dict, cfg) -> list[Moment]:
    """No Gemini: cut the densest speech windows so the pipeline still produces clips."""
    words = transcript.get("words") or []
    segments = transcript.get("segments") or []
    if not words and not segments:
        return _stub_moments(info, transcript, cfg)

    target = float(cfg.get("video.target_max_seconds", 38))
    min_dur = float(cfg.get("video.clip_min_seconds", 15))
    max_count = int(cfg.get("video.max_clips_per_video", 6))
    text = " ".join((w.get("text") or "") for w in words).lower()
    topic = "gta6" if "gta" in text or "gta" in (info.path.name or "").lower() else "gaming"

    candidates = []
    for seg in segments:
        start = float(seg["start"])
        end = min(info.duration, start + target)
        window = " ".join(
            (w.get("text") or "") for w in words if start <= w["start"] <= end
        ).lower()
        density = len(window.split()) / max(1.0, end - start)
        emotion = sum(1 for kw in EMOTION_WORDS if kw in window)
        candidates.append((density * 2 + emotion, start, end, window))

    candidates.sort(reverse=True)
    moments: list[Moment] = []
    for _, start, end, window in candidates:
        if end - start < min_dur:
            continue
        if any(start < m.end and end > m.start for m in moments):
            continue
        hook = _hook_from_text(window)
        moments.append(
            Moment(
                start=round(start, 2),
                end=round(end, 2),
                title=_title_from_text(window) or f"{topic.upper()} moment",
                hook=hook,
                description=f"Clip from the source video. #{topic} #shorts #gaming #clips #fyp",
                hashtags=[f"#{topic}", "#shorts", "#gaming", "#clips", "#fyp"],
                scores={"hook": 6, "emotion": 6, "payoff": 5, "clarity": 6, "loop": 5, "safety": 8},
                reason="Offline fallback: densest action window.",
            )
        )
        if len(moments) >= max_count:
            break
    return sorted(moments, key=lambda m: m.start)


def _hook_from_text(text: str) -> str:
    words = [w.strip(".,!?\"'").upper() for w in re.findall(r"[A-Za-z']{3,}", text)]
    words = [w for w in words if w not in {"THE", "AND", "THIS", "THAT", "WITH", "FROM", "WHAT"}]
    return " ".join(words[:4]) or "WAIT FOR IT"


def _title_from_text(text: str) -> str:
    words = re.findall(r"[A-Za-z']{3,}", text)
    if not words:
        return ""
    snippet = " ".join(words[:11])
    return snippet[:1].upper() + snippet[1:]
