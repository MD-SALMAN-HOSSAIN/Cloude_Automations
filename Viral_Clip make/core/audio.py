from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from .ffmpeg_tools import ffmpeg_bin
from .util import run


def decode_audio(path: Path, max_seconds: float, sample_rate: int = 16000) -> np.ndarray:
    """Decode media to mono float32 PCM with FFmpeg (no PyAV dependency)."""
    cmd = [
        ffmpeg_bin(), "-hide_banner", "-nostdin", "-v", "error",
        "-i", str(path), "-vn", "-ac", "1", "-ar", str(sample_rate),
        "-t", f"{max_seconds:.1f}", "-f", "s16le", "-",
    ]
    proc = run(cmd, timeout=max(120, int(max_seconds * 2)), text=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"could not decode audio from {path.name}: {(proc.stderr or '')[:300]}"
        )
    audio = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    return audio


def rms_curve(audio: np.ndarray, sample_rate: int = 16000, hop: float = 0.25) -> list[float]:
    """RMS energy per `hop` seconds - used by the local viral-moment scorer."""
    if audio.size == 0:
        return []
    step = max(1, int(sample_rate * hop))
    values = []
    for i in range(0, audio.size, step):
        chunk = audio[i:i + step]
        if chunk.size == 0:
            continue
        values.append(float(np.sqrt(np.mean(chunk ** 2))))
    return values
