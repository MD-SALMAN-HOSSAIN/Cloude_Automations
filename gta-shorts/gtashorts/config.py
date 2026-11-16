from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path
from typing import Any

try:  # python-dotenv is optional at import time
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: dict[str, Any] = {
    "paths": {"data_dir": "data", "output_dir": "Ready clips"},
    "video": {
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "crf": 20,
        "preset": "medium",
        "clip_min_seconds": 12,
        "clip_max_seconds": 45,
        "target_min_seconds": 18,
        "target_max_seconds": 32,
        "max_clips_per_video": 6,
        "crop_padding": 0.06,
        "caption_font": "Impact",
        "caption_size": 90,
        "caption_highlight": "&H0000E5FF",
        "hook_size": 112,
        "hook_duration": 3.0,
        "safe_bottom_px": 430,
        "loudness_lufs": -14.0,
        "true_peak_db": -1.5,
    },
    "whisper": {
        "model": "base",
        "device": "cpu",
        "compute_type": "int8",
        "language": "en",
    },
    "gemini": {
        "model": "gemini-3.5-flash",
        "api_key_env": "GEMINI_API_KEY",
        "candidates": 15,
        "min_viral_score": 70,
        "max_retries": 2,
        "use_video_input": False,
    },
    "scoring": {
        "ai_weight": 0.60,
        "min_face_coverage": 0.35,
        "max_words_per_sec": 4.6,
    },
    "scheduler": {
        "times": ["09:00", "13:00", "18:00", "21:00"],
        "timezone": "Asia/Kolkata",
        "buffer_target": 12,
        "refill_minutes": 20,
        "catch_up": True,
        "min_gap_minutes": 150,
        "cleanup_days": 7,
    },
    "youtube": {
        "enabled": True,
        "privacy": "public",
        "category_id": "20",
        "made_for_kids": False,
        "default_tags": ["gta6", "gta6shorts", "gta", "gaming", "shorts"],
        "schedule_uploads": False,
        "client_secret_file": "data/client_secret.json",
        "token_file": "data/token.json",
    },
    "notify": {"discord_webhook": ""},
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _parse_env_file(path: Path) -> None:
    """Minimal .env parser used when python-dotenv is unavailable."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


class Config:
    def __init__(self, data: dict | None = None, root: Path = ROOT):
        self.root = Path(root).resolve()
        self._data = _deep_merge(DEFAULTS, data or {})
        self.env_file = self.root / ".env"
        if load_dotenv is not None:
            load_dotenv(self.env_file, override=False)
        else:
            _parse_env_file(self.env_file)

    # -- generic access ------------------------------------------------
    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self._data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def __getitem__(self, dotted: str) -> Any:
        return self.get(dotted)

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        cur = self._data
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[parts[-1]] = value

    # -- paths ---------------------------------------------------------
    def path(self, key: str) -> Path:
        raw = self.get(f"paths.{key}", key)
        p = Path(raw)
        return p if p.is_absolute() else (self.root / p)

    @property
    def data_dir(self) -> Path:
        return self.path("data_dir")

    @property
    def output_dir(self) -> Path:
        return self.path("output_dir")

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.output_dir, self.work_dir, self.cache_dir, self.models_dir):
            d.mkdir(parents=True, exist_ok=True)

    # -- env -----------------------------------------------------------
    def env(self, name: str, default: str | None = None) -> str | None:
        value = os.environ.get(name, default)
        if value is not None:
            value = value.strip()
        return value or default

    def gemini_key(self) -> str | None:
        return self.env(self.get("gemini.api_key_env", "GEMINI_API_KEY"))

    def timezone(self) -> str:
        return self.get("scheduler.timezone", "UTC")


def load_config(path: Path | str | None = None, root: Path | None = None) -> Config:
    root = Path(root or ROOT)
    cfg_path = Path(path) if path else root / "config.toml"
    data: dict = {}
    if cfg_path.exists():
        with cfg_path.open("rb") as fh:
            data = tomllib.load(fh)
    cfg = Config(data, root=root)
    cfg.ensure_dirs()
    return cfg
