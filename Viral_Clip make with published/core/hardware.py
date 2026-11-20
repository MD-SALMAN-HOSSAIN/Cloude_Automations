"""Hardware detection: pick the fastest free accelerator that actually exists.

Everything here is optional. If no NVIDIA GPU / hardware encoder is present the
app silently uses the CPU path, which is what it has always done.
"""
from __future__ import annotations

import re
from functools import lru_cache

from .util import run

_GPU_NAME = ""
_CUDA_DISABLED = False


@lru_cache(maxsize=1)
def cuda_device_count() -> int:
    """Number of CUDA devices reported by ctranslate2 / torch."""
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count())
    except Exception:
        pass
    try:
        import torch

        return 1 if torch.cuda.is_available() else 0
    except Exception:
        return 0


def disable_cuda(reason: str = ""):
    """Called if a CUDA model loads but fails at runtime - never try it again."""
    global _CUDA_DISABLED
    _CUDA_DISABLED = True
    cuda_runtime_ok.cache_clear()


@lru_cache(maxsize=1)
def cuda_runtime_ok() -> bool:
    """A GPU being present is not enough: cuBLAS 12 must be loadable as well."""
    if _CUDA_DISABLED or cuda_device_count() <= 0:
        return False
    try:
        import ctypes

        def loadable(name: str) -> bool:
            try:
                ctypes.CDLL(name)
                return True
            except OSError:
                return False

        # ctranslate2 ships for CUDA 12 today; accept 11 as well for other builds.
        for pair in (("cublas64_12.dll", "cublasLt64_12.dll"),
                     ("cublas64_11.dll", "cublasLt64_11.dll")):
            if all(loadable(dll) for dll in pair):
                return True
        return False
    except Exception:
        return False


@lru_cache(maxsize=1)
def gpu_name() -> str:
    global _GPU_NAME
    if _GPU_NAME:
        return _GPU_NAME
    if cuda_device_count() <= 0:
        return ""
    for command in (["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                    ["nvidia-smi", "-L"]):
        try:
            proc = run(command, timeout=8)
            text = (proc.stdout or "").strip().splitlines()
            if proc.returncode == 0 and text:
                name = text[0].replace("GPU 0:", "").strip()
                name = re.sub(r"\s*\(UUID:.*", "", name)
                _GPU_NAME = name[:80]
                break
        except Exception:
            continue
    if not _GPU_NAME:
        _GPU_NAME = "NVIDIA CUDA GPU" if cuda_device_count() else ""
    return _GPU_NAME


@lru_cache(maxsize=1)
def ffmpeg_encoders() -> set[str]:
    names: set[str] = set()
    try:
        from .ffmpeg_tools import ffmpeg_bin

        proc = run([ffmpeg_bin(), "-hide_banner", "-encoders"], timeout=20)
        text = proc.stdout or ""
        for line in text.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                names.add(parts[1].lower())
    except Exception:
        pass
    return names


@lru_cache(maxsize=1)
def gpu_encoders() -> list[str]:
    available = ffmpeg_encoders()
    return [enc for enc in ("h264_nvenc", "h264_qsv", "h264_amf") if enc in available]


def preferred_encoder(cfg) -> str:
    """Resolve the user's choice into an actual FFmpeg encoder name."""
    choice = str(cfg.get("render.encoder", "auto") or "auto").lower()
    if choice == "cpu":
        return "libx264"
    options = gpu_encoders()
    if choice == "gpu" and not options:
        return "libx264"  # render_clip will log the fallback
    if choice in ("auto", "gpu") and options:
        return options[0]
    return "libx264"


def whisper_device(cfg) -> tuple[str, str]:
    """Return (device, compute_type) for faster-whisper, with safe fallbacks."""
    choice = str(cfg.get("whisper.device", "auto") or "auto").lower()
    compute = str(cfg.get("whisper.compute_type", "auto") or "auto").lower()
    has_cuda = cuda_runtime_ok()
    device = "cuda" if choice in ("cuda", "gpu") else "cpu"
    if choice in ("auto", ""):
        device = "cuda" if has_cuda else "cpu"
    if device == "cuda" and not has_cuda:
        device = "cpu"
    if compute in ("auto", ""):
        compute = "float16" if device == "cuda" else "int8"
    if device == "cpu" and compute.startswith("float"):
        compute = "int8"
    return device, compute


def describe() -> dict:
    """Short summary used by the GUI and the doctor report."""
    encoders = gpu_encoders()
    device, compute = whisper_device({"render.encoder": "auto",
                                      "whisper.device": "auto",
                                      "whisper.compute_type": "auto"})
    return {
        "cuda_detected": cuda_device_count() > 0,
        "cuda": cuda_runtime_ok(),
        "name": gpu_name(),
        "encoders": encoders,
        "encoder": preferred_encoder({"render.encoder": "auto"}),
        "whisper_device": device,
        "compute": compute,
    }


def summary_line() -> str:
    info = describe()
    gpu = info["name"] or "none detected"
    enc = ", ".join(info["encoders"]) or "none"
    speech_note = "" if info["cuda"] or not info["cuda_detected"] else " (CUDA runtime DLLs missing)"
    return (f"GPU: {gpu} | speech device: {info['whisper_device']}{speech_note} "
            f"({info['compute']}) | video encoder: {info['encoder']} | "
            f"hardware encoders: {enc}")
