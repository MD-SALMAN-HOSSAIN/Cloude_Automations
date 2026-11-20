"""Optional AI moment selection. Local scoring stays the free default; if a user
supplies an API key/model, the AI picks and writes the clips instead."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .moments import Moment
from .util import Logger, clamp

# Google retired gemini-2.5-flash for new API keys and the newest flash models
# are often overloaded, so default to a fast, widely available one.
GEMINI_DEFAULT_MODEL = "gemini-3.5-flash-lite"

# Tried in order if the configured model is unavailable (404/429/503).
GEMINI_FALLBACK_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.7-flash",
    "gemini-3.8-flash",
]


def _retryable(exc: Exception) -> bool:
    text = str(exc).upper()
    return any(code in text for code in
               ("404", "429", "503", "NOT_FOUND", "UNAVAILABLE", "RESOURCE_EXHAUSTED"))


def _gemini_generate(client, cfg, contents, gen_cfg=None):
    """Generate with automatic fallback to another available Gemini model."""
    configured = str(cfg.get("ai.model", "") or "").strip()
    models = ([configured] if configured else []) + [
        m for m in GEMINI_FALLBACK_MODELS if m != configured
    ]
    last = None
    for name in models:
        try:
            response = client.models.generate_content(
                model=name, contents=contents, config=gen_cfg
            )
            if name != configured:
                cfg.set("ai.model", name)  # remember the working model
            return response
        except Exception as exc:
            if not _retryable(exc):
                raise  # invalid key / auth errors must not be hidden
            last = exc
    raise last


def _extract_json(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    starts = [i for i in (text.find("["), text.find("{")) if i >= 0]
    ends = [i for i in (text.rfind("]"), text.rfind("}")) if i >= 0]
    if starts and ends:
        start, end = min(starts), max(ends)
        if end > start:
            return text[start:end + 1]
    return text


def ask_json(prompt: str, cfg):
    """Ask the configured provider for JSON (list or object). Returns None in local mode."""
    provider = str(cfg.get("ai.provider", "local") or "local").lower()
    if provider == "gemini":
        raw = _call_gemini(prompt, cfg)
    elif provider == "openai":
        raw = _call_openai_compatible(prompt, cfg)
    else:
        return None
    return json.loads(_extract_json(raw))


def _call_gemini(prompt: str, cfg) -> str:
    from google import genai

    key = str(cfg.get("ai.api_key", "") or "").strip()
    if not key:
        raise RuntimeError("Gemini API key is empty. Add it in the AI tab or choose Local mode.")
    client = genai.Client(api_key=key)
    try:
        from google.genai import types
        gen_cfg = types.GenerateContentConfig(max_output_tokens=8192)
    except Exception:
        gen_cfg = None
    response = _gemini_generate(client, cfg, prompt, gen_cfg=gen_cfg)
    return response.text or ""


def _call_openai_compatible(prompt: str, cfg) -> str:
    import requests

    base = str(cfg.get("ai.base_url", "") or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("Base URL is empty (e.g. http://localhost:11434/v1).")
    key = str(cfg.get("ai.api_key", "") or "").strip()
    model = str(cfg.get("ai.model", "") or "").strip()
    if not model:
        raise RuntimeError("Model name is empty.")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    response = requests.post(
        base + "/chat/completions",
        headers=headers,
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        },
        timeout=240,
    )
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"]


def _build_prompt(transcript: dict, cfg) -> str:
    duration = float(transcript.get("duration") or 0)
    min_len = float(cfg.get("general.min_seconds", 12))
    max_len = float(cfg.get("general.max_seconds", 45))
    wanted = int(cfg.get("general.clips_per_video", 5))

    segments = transcript.get("segments") or []
    lines = []
    used = 0
    for seg in segments:
        line = f"[{float(seg['start']):.1f}-{float(seg['end']):.1f}] {seg.get('text','').strip()}"
        if used + len(line) > 24000:
            break
        lines.append(line)
        used += len(line)
    transcript_text = "\n".join(lines) or transcript.get("text", "")

    return f"""You are a viral short-form video editor. Pick the {wanted} most engaging moments
from the transcript below and write metadata for each one.

Video duration: {duration:.1f} seconds.
Each moment MUST be {min_len:.0f}-{max_len:.0f} seconds long, start and end on complete
sentences where possible, and stay inside 0 to {duration:.1f} seconds.
Prefer moments with a strong hook, emotion, surprise, a question or a clear payoff.
The title must be <=70 characters. The hook must be <=8 words, ALL CAPS, and punchy.
The reason must be a short phrase (max 6 words). The score is 0-100 (higher = more viral).

Return ONLY a JSON array, no markdown and no explanation, in this exact shape:
[{{"start":12.5,"end":34.0,"title":"...","hook":"...","reason":"...","score":88}}]

TRANSCRIPT:
{transcript_text}
"""


def _snap(value: float, segments: list[dict], tolerance: float = 2.0) -> float:
    if not segments:
        return value
    nearest = min(segments, key=lambda s: abs(float(s["start"]) - value))
    if abs(float(nearest["start"]) - value) <= tolerance:
        return float(nearest["start"])
    return value


def enhance_moments(transcript: dict, local: list[Moment], cfg, log: Logger) -> list[Moment] | None:
    """Return AI-picked moments, or None to keep the local free picks."""
    provider = str(cfg.get("ai.provider", "local") or "local").lower()
    if provider == "local":
        return None

    log.info("Asking %s model '%s' to pick moments", provider, cfg.get("ai.model", ""))
    try:
        prompt = _build_prompt(transcript, cfg)
        if provider == "gemini":
            raw = _call_gemini(prompt, cfg)
        elif provider == "openai":
            raw = _call_openai_compatible(prompt, cfg)
        else:
            raise RuntimeError(f"Unknown AI provider: {provider}")

        data = json.loads(_extract_json(raw))
        if not isinstance(data, list):
            raise RuntimeError("AI response was not a list of moments")

        duration = float(transcript.get("duration") or 0)
        min_len = float(cfg.get("general.min_seconds", 12))
        max_len = float(cfg.get("general.max_seconds", 45))
        segments = transcript.get("segments") or []
        words = transcript.get("words") or []

        picked: list[Moment] = []
        for item in data:
            try:
                start = float(item["start"])
                end = float(item["end"])
            except (KeyError, TypeError, ValueError):
                continue
            start = _snap(clamp(start, 0, max(0, duration - min_len)), segments)
            end = _snap(clamp(end, start + min_len, min(duration, start + max_len)), segments)
            if end <= start:
                end = min(duration, start + min_len)
            if end - start < min_len or end > duration + 0.5:
                continue
            count = sum(1 for w in words if start <= float(w["start"]) < end)
            picked.append(Moment(
                start=round(start, 2), end=round(end, 2),
                score=round(clamp(float(item.get("score", 75)), 0, 100), 1),
                title=str(item.get("title") or "Viral moment").strip()[:70],
                hook=str(item.get("hook") or "WATCH THIS").strip().upper()[:60],
                reason=str(item.get("reason") or "AI selection").strip()[:80],
                words=count,
            ))

        if not picked:
            raise RuntimeError("AI returned no usable moments")

        # Remove overlapping picks and cap to the requested count.
        picked.sort(key=lambda m: m.score, reverse=True)
        kept: list[Moment] = []
        for moment in picked:
            if any(
                min(moment.end, other.end) - max(moment.start, other.start) > 0
                and (min(moment.end, other.end) - max(moment.start, other.start))
                / min(moment.end - moment.start, other.end - other.start) >= 0.4
                for other in kept
            ):
                continue
            kept.append(moment)
            if len(kept) >= int(cfg.get("general.clips_per_video", 5)):
                break
        kept.sort(key=lambda m: m.start)
        log.ok("AI selected %d moments", len(kept))
        return kept
    except Exception as exc:
        log.warn("AI selection failed (%s) - falling back to free local scoring", exc)
        return None


def test_connection(cfg, log: Logger | None = None) -> dict:
    """Live end-to-end check of the configured provider. Never raises.

    Returns: {ok, provider, model, message, latency_ms}
    """
    import time

    provider = str(cfg.get("ai.provider", "local") or "local").lower()
    model = str(cfg.get("ai.model", "") or "").strip()
    started = time.perf_counter()

    def result(ok: bool, message: str) -> dict:
        return {
            "ok": bool(ok),
            "provider": provider,
            "model": model,
            "message": message,
            "latency_ms": max(0, int((time.perf_counter() - started) * 1000)),
        }

    def err(exc: Exception) -> str:
        text = " ".join(str(exc).split())
        return (text or exc.__class__.__name__)[:600]

    if provider == "local":
        return result(True, "Local engine ready - no API needed")

    if provider == "gemini":
        key = str(cfg.get("ai.api_key", "") or "").strip()
        if not key:
            return result(False, "No API key entered - add your Gemini key above")
        try:
            from google import genai

            client = genai.Client(api_key=key)
            try:
                from google.genai import types
                gen_cfg = types.GenerateContentConfig(max_output_tokens=16)
            except Exception:
                gen_cfg = None
            _gemini_generate(client, cfg, "Reply with OK", gen_cfg=gen_cfg)
            working = str(cfg.get("ai.model", "") or model or GEMINI_DEFAULT_MODEL)
            return result(True, f"Connected - {working} responded")
        except Exception as exc:
            return result(False, f"Gemini: {err(exc)}")

    if provider == "openai":
        base = str(cfg.get("ai.base_url", "") or "").strip().rstrip("/")
        if not base:
            return result(False, "No Base URL entered")
        if not model:
            return result(False, "No model name entered")
        key = str(cfg.get("ai.api_key", "") or "").strip()
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        try:
            import requests

            payload = {
                "model": model,
                "messages": [{"role": "user", "content": "Reply with OK"}],
                "max_tokens": 8,
                "temperature": 0,
            }
            response = requests.post(base + "/chat/completions", headers=headers,
                                     json=payload, timeout=30)
            if response.status_code == 400:
                # Some reasoning models reject max_tokens - retry without it.
                payload.pop("max_tokens", None)
                response = requests.post(base + "/chat/completions", headers=headers,
                                         json=payload, timeout=30)
            if response.status_code >= 400:
                detail = " ".join(response.text.split())[:120]
                return result(False, f"HTTP {response.status_code}: {detail}")
            response.json()["choices"][0]["message"]["content"]
            return result(True, f"Connected - {model} responded")
        except Exception as exc:
            return result(False, err(exc))

    return result(False, f"Unknown provider: {provider}")
