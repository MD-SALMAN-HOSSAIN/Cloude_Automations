"""Regression test for the Video framing on/off toggle.

Off must mean: no AI style query, no local subject tracker, no tracked crop path,
and the Layout template alone composes the clip. Run:
    python framingtoggletest.py
No network or API key needed.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import faces, style
from core.config import Config
from core.ffmpeg_tools import make_test_video
from core.pipeline import run_pipeline
from core.util import Logger


def _blocked(*_args, **_kwargs):
    raise AssertionError("subject tracking ran while Video framing was disabled")


def _capture():
    lines = []
    return Logger(lambda level, msg: lines.append((level, msg))), lines


def test_layout_template_only_pipeline():
    work = Path(tempfile.mkdtemp(prefix="vcm_framing_off_"))
    source = make_test_video(work / "source.mp4", seconds=30)
    cfg = Config(path=work / "settings.json")
    cfg.set("whisper.model", "tiny")
    cfg.set("general.min_seconds", 8)
    cfg.set("general.max_seconds", 16)
    cfg.set("general.clips_per_video", 1)
    cfg.set("general.max_minutes", 1)
    cfg.set("general.output_dir", str(work / "output"))
    cfg.set("features.split_layout", True)
    cfg.set("video.framing_enabled", False)

    log, lines = _capture()
    originals = (faces.analyse_video, faces.build_path,
                 style.choose_styles, style._ai_decide)
    faces.analyse_video = _blocked
    faces.build_path = _blocked
    style.choose_styles = _blocked
    style._ai_decide = _blocked
    try:
        result = run_pipeline(str(source), cfg, log, lambda *_: None)
    finally:
        (faces.analyse_video, faces.build_path,
         style.choose_styles, style._ai_decide) = originals

    clips = result["clips"]
    assert clips, "no clip was rendered with Video framing off"
    assert all(c["layout"] == "split" for c in clips), clips
    assert all(c["style"] == "layout" for c in clips), clips
    assert all(Path(c["path"]).exists() for c in clips), clips
    messages = [msg for _, msg in lines]
    assert any("Video framing disabled — using Layout template directly" in m
               for m in messages), messages
    assert any("Layout: split screen" in m for m in messages), messages
    print("PASS split render used Layout template only (no subject tracking)")


def test_ui_toggle_and_preset():
    import customtkinter as ctk
    from gui import main_window as window

    work = Path(tempfile.mkdtemp(prefix="vcm_framing_ui_"))
    settings = work / "settings.json"
    root = ctk.CTk()
    root.withdraw()
    original_config, original_cleanup = window.Config, window.App._cleanup_startup
    window.Config = lambda: Config(path=settings)
    window.App._cleanup_startup = lambda self: None
    try:
        app = window.App(root)
        assert app.framing_toggle_var.get() is True
        assert app.framing_segments.cget("state") == "normal"

        app.framing_toggle_var.set(False)
        app._on_framing_toggle()
        assert app.framing_segments.cget("state") == "disabled"
        assert app.framing_off_badge.winfo_manager() == "pack"
        app._save_settings()
        saved = json.loads(settings.read_text(encoding="utf-8"))
        assert saved["video.framing_enabled"] is False, saved

        # Applying the viral preset must not silently re-enable framing.
        app.apply_viral_preset()
        assert app.framing_toggle_var.get() is False
        assert app.framing_segments.cget("state") == "disabled"

        app.framing_toggle_var.set(True)
        app._on_framing_toggle()
        assert app.framing_segments.cget("state") == "normal"
        assert app.framing_off_badge.winfo_manager() == ""
        app._save_settings()
        saved = json.loads(settings.read_text(encoding="utf-8"))
        assert saved["video.framing_enabled"] is True, saved
    finally:
        window.Config, window.App._cleanup_startup = original_config, original_cleanup
        root.destroy()
    print("PASS toggle dims controls, persists, and survives the viral preset")


if __name__ == "__main__":
    test_layout_template_only_pipeline()
    test_ui_toggle_and_preset()
    print("framing toggle: all checks passed")
