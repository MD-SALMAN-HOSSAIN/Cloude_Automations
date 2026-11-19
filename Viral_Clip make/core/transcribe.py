from __future__ import annotations

import json
from pathlib import Path

from .audio import decode_audio
from .util import Logger

_MODEL = None
_MODEL_KEY = None


def _load_model(cfg, log: Logger):
    global _MODEL, _MODEL_KEY
    key = (cfg.get("whisper.model", "base"), cfg.get("whisper.device", "cpu"),
           cfg.get("whisper.compute_type", "int8"))
    if _MODEL is not None and _MODEL_KEY == key:
        return _MODEL
    from faster_whisper import WhisperModel

    cfg.models_dir.mkdir(parents=True, exist_ok=True)
    log.info("Loading local speech model '%s' (first run downloads it once)", key[0])
    _MODEL = WhisperModel(
        key[0], device=key[1], compute_type=key[2],
        download_root=str(cfg.models_dir),
    )
    _MODEL_KEY = key
    return _MODEL


def transcribe(path: Path, cfg, log: Logger, audio: "object | None" = None,
               force: bool = False) -> dict:
    """Word-level transcript using faster-whisper - fully local, no API key."""
    cache = Path(str(path) + ".transcript.json")
    if cache.exists() and not force:
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if cached and cached.get("words") is not None:
                log.info("Using cached transcript (%d words)", len(cached["words"]))
                return cached
        except Exception:
            pass

    model = _load_model(cfg, log)
    max_seconds = float(cfg.get("general.max_minutes", 45)) * 60
    if audio is None:
        log.info("Decoding audio for transcription")
        audio = decode_audio(path, max_seconds)
    log.info("Transcribing %.1f min of audio locally (this is the slow step on CPU)",
             audio.size / 16000 / 60)

    segments, info = model.transcribe(
        audio,
        language=cfg.get("general.language") or None,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
        beam_size=5,
        condition_on_previous_text=False,
    )

    words, segs = [], []
    for segment in segments:
        segs.append({
            "start": round(float(segment.start), 3),
            "end": round(float(segment.end), 3),
            "text": (segment.text or "").strip(),
        })
        for word in getattr(segment, "words", None) or []:
            text = (word.word or "").strip()
            if not text:
                continue
            words.append({
                "start": round(float(word.start), 3),
                "end": round(float(word.end), 3),
                "text": text,
            })

    data = {
        "language": getattr(info, "language", None),
        "duration": round(float(getattr(info, "duration", 0) or 0), 3),
        "segments": segs,
        "words": words,
        "text": " ".join(s["text"] for s in segs).strip(),
    }
    try:
        cache.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass
    log.info("Transcript ready: %d words, language=%s", len(words), data["language"])
    return data
