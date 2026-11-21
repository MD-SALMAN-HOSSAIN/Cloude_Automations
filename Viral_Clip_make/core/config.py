from __future__ import annotations

import json
import os
from pathlib import Path

from .util import app_base_dir


def app_data_dir() -> Path:
    """Writable folder for models, caches and settings (works when frozen in Program Files)."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = Path(base) / "ViralClipMake"
    path.mkdir(parents=True, exist_ok=True)
    (path / "models").mkdir(exist_ok=True)
    (path / "cache").mkdir(exist_ok=True)
    (path / "downloads").mkdir(exist_ok=True)
    (path / "work").mkdir(exist_ok=True)
    return path


def default_settings() -> dict:
    return {
        "general.output_dir": str(Path.home() / "Videos" / "Viral Clip Make Output"),
        "general.clips_per_video": 5,
        "general.min_seconds": 12,
        "general.max_seconds": 45,
        "general.language": "",
        "general.max_minutes": 45,
        "general.download_height": 720,
        # YouTube sometimes challenges anonymous downloads. "auto" tries
        # anonymous first, then cookies from installed browsers (Firefox first).
        "download.cookies_from_browser": "auto",  # auto | none | firefox | chrome | edge | ...
        "download.cookies_file": "",              # optional Netscape cookies.txt path
        "download.player_client": "default",      # default | tv | android | ios | ...
        "video.width": 1080,
        "video.height": 1920,
        "video.fps": 30,
        "video.crf": 20,
        "video.preset": "medium",
        "whisper.model": "base",
        "whisper.device": "auto",          # auto | cpu | cuda
        "whisper.compute_type": "auto",    # auto -> int8 on CPU, float16 on GPU
        "render.encoder": "auto",          # auto | cpu | gpu (falls back to CPU)
        "video.framing": "auto",           # auto (AI picks) | fit_blur | fit_black | track
        "video.subject_focus_y": 0.42,     # subject sits above captions in a tracked crop
        "video.crop_padding": 0.06,        # keep this margin around a tracked subject
        "ai.auto_style": True,             # let the AI choose framing per clip when available
        "features.face_track": True,
        "features.captions": True,
        "features.hook": True,
        "features.normalize_audio": True,
        "style.caption_font": "Impact",
        "style.caption_size": 88,
        "style.caption_highlight": "&H0000E5FF",
        "style.hook_size": 104,
        "style.hook_duration": 2.8,
        "style.safe_bottom_px": 430,
        "video.crop_padding": 0.06,
        "video.loudness_lufs": -14.0,
        "video.true_peak_db": -1.5,
        "moments.ai_weight": 0.60,
        # Optional AI / API (local mode stays the free default)
        "ai.provider": "local",          # local | gemini | openai
        "ai.api_key": "",
        "ai.base_url": "http://localhost:11434/v1",
        "ai.claude_base_url": "https://api.anthropic.com/v1",
        "ai.model": "gemini-3.5-flash-lite",
        # Output / quality
        "quality.mode": "balanced",      # fast | balanced | best
        "video.aspect": "9:16",          # 9:16 | 1:1 | 4:5 | 3:4 | 16:9 | custom
        "video.custom_width": 1080,
        "video.custom_height": 1920,
        "speed.face_interval": 0.5,
        "speed.candidates": 600,
    }


class Config:
    def __init__(self, path: Path | None = None):
        self.path = path or (app_data_dir() / "settings.json")
        self.data = default_settings()
        self.load()

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value):
        self.data[key] = value

    def update(self, values: dict):
        self.data.update(values)

    @property
    def models_dir(self) -> Path:
        p = app_data_dir() / "models"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def cache_dir(self) -> Path:
        p = app_data_dir() / "cache"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def downloads_dir(self) -> Path:
        p = app_data_dir() / "downloads"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def work_dir(self) -> Path:
        p = app_data_dir() / "work"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def load(self):
        try:
            if self.path.exists():
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(saved, dict):
                    self.data.update(saved)
                    # Older installs had no hardware choices. Their saved "cpu"
                    # was an invisible default, not a real decision - let Auto
                    # pick the newly-detected GPU instead of silently staying slow.
                    if ("render.encoder" not in saved and "video.framing" not in saved
                            and self.data.get("whisper.device") == "cpu"
                            and self.data.get("whisper.compute_type") == "int8"):
                        self.data["whisper.device"] = "auto"
                        self.data["whisper.compute_type"] = "auto"
                    # One-time upgrade: installs saved before AI styling existed used a
                    # fixed framing. Flip them to the new smart default.
                    if "ai.auto_style" not in saved and self.data.get("video.framing") in (
                            "fit_blur", "fit_black", "track"):
                        self.data["video.framing"] = "auto"
        except Exception:
            pass

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        except Exception:
            pass
