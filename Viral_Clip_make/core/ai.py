"""Optional AI moment selection. Local scoring stays the free default; if a user
supplies an API key/model, the AI picks and writes the clips instead."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .moments import Moment
from .util import Logger, clamp

# Measured on this machine (Nov 2026): 3.5-flash-lite answers the moment-picking
# prompt in ~3 s, while 3.8-flash takes ~100 s and can return an empty body.
# Fast and reliable is what makes the app feel like it is working.
GEMINI_DEFAULT_MODEL = "gemini-3.5-flash-lite"
GEMINI_REQUEST_TIMEOUT = 30  # seconds - never let a slow model freeze a run

# Anthropic's current fast/cheap model for this kind of short structured task.
CLAUDE_DEFAULT_MODEL = "claude-sonnet-5-5"
CLAUDE_DEFAULT_BASE = "https://api.anthropic.com/v1"
CLAUDE_FALLBACK_MODELS = [
    "claude-sonnet-5-5",
    "claude-haiku-5-5",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
]

# Tried in order if the configured model is unavailable (404/429/503) or returns
# an empty body. Fastest first; slow/unreliable models are last resorts only.
GEMINI_FALLBACK_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.8-flash",
]


def _retryable(exc: Exception) -> bool:
    text = str(exc).upper()
    return any(code in text for code in
               ("404", "429", "503", "NOT_FOUND", "UNAVAILABLE", "RESOURCE_EXHAUSTED",
                "EMPTY RESPONSE"))


def _gemini_generate(client, cfg, contents, gen_cfg=None, require_text=True):
    """Generate with automatic fallback to another available Gemini model.

    A response with no text is treated as a failure: some models answer with an
    empty body after a very long delay, and remembering them as "working" is what
    previously left installs stuck on a 100-second model.
    """
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
            if require_text and not (getattr(response, "text", "") or "").strip():
                raise RuntimeError(f"{name} returned an empty response")
            if name != configured:
                cfg.set("ai.model", name)  # remember the fast model that worked
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
    elif provider == "claude":
        raw = _call_claude(prompt, cfg)
    elif provider == "openai":
        raw = _call_openai_compatible(prompt, cfg)
    else:
        return None
    return json.loads(_extract_json(raw))


def _gemini_client(cfg):
    from google import genai

    key = str(cfg.get("ai.api_key", "") or "").strip()
    if not key:
        raise RuntimeError("Gemini API key is empty. Add it in the AI tab or choose Local mode.")
    # A hard request timeout keeps a stalled model from hanging the whole app.
    try:
        from google.genai import types
        return genai.Client(
            api_key=key,
            http_options=types.HttpOptions(timeout=GEMINI_REQUEST_TIMEOUT * 1000),
        )
    except Exception:
        return genai.Client(api_key=key)


def _call_gemini(prompt: str, cfg) -> str:
    client = _gemini_client(cfg)
    try:
        from google.genai import types
        gen_cfg = types.GenerateContentConfig(max_output_tokens=8192)
    except Exception:
        gen_cfg = None
    response = _gemini_generate(client, cfg, prompt, gen_cfg=gen_cfg)
    return response.text or ""


def _claude_request(prompt: str, cfg, model: str, max_tokens: int) -> str:
    """One request to Anthropic's Messages API. Returns the concatenated text."""
    import requests

    key = str(cfg.get("ai.api_key", "") or "").strip()
    if not key:
        raise RuntimeError("Claude API key is empty. Add it in the AI tab or choose Local mode.")
    base = str(cfg.get("ai.claude_base_url", "") or CLAUDE_DEFAULT_BASE).strip().rstrip("/")
    if not base:
        base = CLAUDE_DEFAULT_BASE

    response = requests.post(
        base + "/messages",
        headers={
            "Content-Type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
        json={
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=240,
    )
    response.raise_for_status()
    data = response.json()
    blocks = data.get("content") or []
    text = "".join(
        str(block.get("text") or "")
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
    )
    if not text:
        raise RuntimeError("Claude returned an empty response")
    return text


def _claude_generate(prompt: str, cfg, max_tokens: int) -> str:
    """Try the configured Claude model, then a few widely available fallbacks."""
    configured = str(cfg.get("ai.model", "") or "").strip() or CLAUDE_DEFAULT_MODEL
    models = [configured] + [m for m in CLAUDE_FALLBACK_MODELS if m != configured]
    last = None
    for name in models:
        try:
            text = _claude_request(prompt, cfg, name, max_tokens)
            if name != configured:
                cfg.set("ai.model", name)  # remember the model that actually works
            return text
        except Exception as exc:
            if not _retryable(exc):
                raise  # bad key / permission errors must not be hidden
            last = exc
    raise last


def _call_claude(prompt: str, cfg) -> str:
    return _claude_generate(prompt, cfg, max_tokens=8192)


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
        elif provider == "claude":
            raw = _call_claude(prompt, cfg)
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
        # Lead with the highest-scoring moment so the first exported clip is
        # the one with the best chance of taking off.
        kept.sort(key=lambda m: m.score, reverse=True)
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
            client = _gemini_client(cfg)
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

    if provider == "claude":
        key = str(cfg.get("ai.api_key", "") or "").strip()
        if not key:
            return result(False, "No API key entered - add your Claude key above")
        check_model = model or CLAUDE_DEFAULT_MODEL
        try:
            text = _claude_generate("Reply with OK", cfg, max_tokens=16)
            working = str(cfg.get("ai.model", "") or check_model)
            return result(True, f"Connected - {working} responded: {text[:40]}")
        except Exception as exc:
            return result(False, f"Claude: {err(exc)}")

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
