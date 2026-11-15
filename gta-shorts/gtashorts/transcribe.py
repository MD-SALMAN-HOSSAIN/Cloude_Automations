from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from .ffmpeg import ffmpeg_bin
from .util import LOG, read_json, write_json

_MODEL = None


def _load_model(cfg):
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    from faster_whisper import WhisperModel

    cfg.models_dir.mkdir(parents=True, exist_ok=True)
    LOG.info("loading whisper model '%s' (first run downloads it)", cfg.get("whisper.model", "base"))
    _MODEL = WhisperModel(
        cfg.get("whisper.model", "base"),
        device=cfg.get("whisper.device", "cpu"),
        compute_type=cfg.get("whisper.compute_type", "int8"),
        download_root=str(cfg.models_dir),
    )
    return _MODEL


def _decode_audio(path: Path, max_seconds: float, sample_rate: int = 16000) -> np.ndarray:
    """Decode to mono float32 with ffmpeg. Avoids PyAV version incompatibilities and is faster."""
    cmd = [
        ffmpeg_bin(), "-hide_banner", "-nostdin", "-v", "error",
        "-i", str(path), "-vn", "-ac", "1", "-ar", str(sample_rate),
        "-t", f"{max_seconds:.1f}", "-f", "s16le", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg could not decode audio from {path.name}: {proc.stderr.decode('utf-8', 'replace')[:300]}")
    audio = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    if audio.size == 0:
        return audio
    return audio


def transcribe(path: Path, cfg, force: bool = False) -> dict:
    """Word-level transcript, cached next to the source file."""
    cache = path.with_suffix(path.suffix + ".transcript.json")
    if cache.exists() and not force:
        cached = read_json(cache)
        if cached and cached.get("words") is not None:
            LOG.info("transcript: using cache (%d words)", len(cached["words"]))
            return cached

    model = _load_model(cfg)
    max_minutes = float(cfg.get("download.max_minutes", 60))
    LOG.info("decoding audio from %s", path.name)
    audio = _decode_audio(path, max_seconds=max_minutes * 60)

    LOG.info("transcribing %.0f min of audio - this is the slow step on CPU", len(audio) / 16000 / 60)
    segments, info = model.transcribe(
        audio,
        language=cfg.get("whisper.language") or None,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
        beam_size=5,
        condition_on_previous_text=False,
    )

    words = []
    segs = []
    for segment in segments:
        segs.append(
            {
                "start": round(float(segment.start), 3),
                "end": round(float(segment.end), 3),
                "text": (segment.text or "").strip(),
            }
        )
        for word in getattr(segment, "words", None) or []:
            text = (word.word or "").strip()
            if not text:
                continue
            words.append(
                {
                    "start": round(float(word.start), 3),
                    "end": round(float(word.end), 3),
                    "text": text,
                }
            )
    data = {
        "language": getattr(info, "language", None),
        "duration": round(float(getattr(info, "duration", 0) or 0), 3),
        "segments": segs,
        "words": words,
        "text": " ".join(s["text"] for s in segs).strip(),
    }
    write_json(cache, data)
    LOG.info("transcript: %d words, %d segments (language=%s)", len(words), len(segs), data["language"])
    return data
