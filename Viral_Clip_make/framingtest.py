"""Regression test: the game character is always the main subject.

Covers every path that used to produce a "whole frame (blurred)" clip:
the free local heuristic, AI styling, manual framing settings, facecam
detection, render fallback and the tracked crop itself. Run:
    python framingtest.py
No network or API key needed (the AI reply is stubbed locally).
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import ai, faces, style
from core.ffmpeg_tools import (centered_crop_xy, crop_geometry, ffmpeg_bin,
                               make_test_video, probe, render_clip, RenderRequest)
from core.util import Logger

log = Logger(lambda level, msg: print(f"[{level}] {msg}"))


class Cfg:
    def __init__(self, **overrides):
        self.data = {
            "video.framing": "auto",
            "video.subject_focus_y": 0.42,
            "video.crop_padding": 0.06,
            "video.width": 1080, "video.height": 1920, "video.fps": 30,
            "video.crf": 26, "video.preset": "veryfast",
            "ai.auto_style": False,
            "ai.provider": "local",
            "features.normalize_audio": False,
            "video.loudness_lufs": -14.0, "video.true_peak_db": -1.5,
            "render.encoder": "cpu",
        }
        self.data.update(overrides)

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


def _moment():
    return SimpleNamespace(start=0.0, end=8.0, title="character moment", hook="CHARACTER")


def _metrics(**overrides):
    m = {
        "face_coverage": 0.0, "motion_coverage": 0.0,
        "cx": 0.5, "cy": 0.45, "spread_x": 0.0, "spread_y": 0.0,
        "edge_hits": 0.0, "samples": 10, "subject": "center",
        "source_ratio": 16 / 9, "target_ratio": 9 / 16,
    }
    m.update(overrides)
    return m


def test_local_never_returns_whole_frame():
    cases = {
        "wide action": _metrics(motion_coverage=0.9, spread_x=0.6, subject="motion"),
        "already vertical": _metrics(source_ratio=9 / 16, target_ratio=9 / 16),
        "no subject": _metrics(),
        "HUD-heavy": _metrics(face_coverage=0.8, edge_hits=0.9, subject="character"),
    }
    for name, metrics in cases.items():
        decision = style.local_decide(metrics, Cfg())
        assert decision.framing == "track", (name, decision)
        assert decision.locked, name
    print("PASS local heuristic never picks fit_blur/fit_black")


def test_manual_whole_frame_is_upgraded():
    cfg = Cfg(**{"video.framing": "fit_blur"})
    decisions = style.choose_styles([_moment(), _moment()], None, cfg, log)
    assert len(decisions) == 2
    assert all(d.framing == "track" for d in decisions), decisions
    assert all("game-subject lock" in d.reason for d in decisions), decisions
    cfg = Cfg(**{"video.framing": "fit_black"})
    assert all(d.framing == "track" for d in style.choose_styles([_moment()], None, cfg, log))
    print("PASS manual whole-frame settings are upgraded to a character crop")


def test_ai_whole_frame_is_rejected():
    original = ai.ask_json
    ai.ask_json = lambda prompt, cfg: [
        {"index": 0, "framing": "fit_blur", "focus_x": 0.5, "focus_y": 0.42,
         "reason": "wide gameplay", "confidence": 0.99}
    ]
    try:
        cfg = Cfg(**{"ai.provider": "gemini", "ai.auto_style": True})
        decisions = style.choose_styles([_moment()], None, cfg, log)
    finally:
        ai.ask_json = original
    assert len(decisions) == 1 and decisions[0].framing == "track", decisions
    print("PASS AI fit_blur suggestion is overridden by the game-subject lock")


def test_webcam_face_cannot_steal_the_subject():
    samples = [{
        "t": i * 0.5,
        "face_x": 0.84, "face_y": 0.18, "face_w": 0.08, "face_h": 0.15,
        "face_conf": 0.95,
        "motion_x": 0.42, "motion_y": 0.50, "motion_conf": 0.7,
    } for i in range(20)]
    track = {"width": 1280, "height": 720, "sample_interval": 0.5, "samples": samples}
    rect = faces.find_facecam(track)
    assert rect and rect["corner"] == "right", rect
    host = faces.subject_point(samples[0])
    game = faces.subject_point(samples[0], rect)
    assert host[2] == "character" and host[0] > 0.8, host
    assert game[2] == "motion" and abs(game[0] - 0.42) < 0.01, game
    print("PASS corner webcam is excluded; crop follows the game action")


def test_character_face_beats_motion_noise():
    sample = {"t": 0.0, "face_x": 0.34, "face_y": 0.42, "face_conf": 0.92,
              "motion_x": 0.85, "motion_y": 0.25, "motion_conf": 0.8}
    point = faces.subject_point(sample)
    assert point[2] == "character" and abs(point[0] - 0.34) < 1e-6, point
    print("PASS detected game character wins over noisy motion")


def test_missing_track_still_centres_the_character():
    cfg = Cfg()
    empty = {"width": 1280, "height": 720, "sample_interval": 0.5, "samples": []}
    path = faces.build_path(empty, 0, 8, 405, 720, cfg)
    assert abs(path["x"][1][0] - 437.5) < 1.0, path
    assert path["y"][1][0] == 0, path
    crop_w, crop_h = crop_geometry(1280, 720, 1080, 1920)
    x, y = centered_crop_xy(1280, 720, crop_w, crop_h)
    assert (x, y) == ((1280 - crop_w) // 2, 0), (x, y)
    print("PASS no-track fallback uses a centred crop, not a corner crop")


def _blue_fraction(png: Path) -> float:
    import cv2

    frame = cv2.imread(str(png))
    assert frame is not None, png
    b, g, r = (frame[:, :, i].astype(np.int16) for i in range(3))
    return float(((r > 200) & (b > 200) & (g < 80)).mean())


def test_render_centres_when_no_path(work: Path):
    src = make_test_video(work / "source.mp4", seconds=5, size="1280x720")
    marked = work / "marked.mp4"
    # A magenta block at x=120 is outside the centred 9:16 crop (x=437..842).
    vf = "drawbox=x=120:y=300:w=100:h=100:color=magenta:t=fill"
    proc = subprocess.run([
        ffmpeg_bin(), "-hide_banner", "-y", "-nostdin", "-i", str(src),
        "-vf", vf, "-c:v", "libx264", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", "-c:a", "copy", str(marked),
    ], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[-600:]
    info = probe(marked)
    cfg = Cfg()

    def render(name, crop_x):
        out = work / name
        render_clip(RenderRequest(
            source=marked, start=1.0, end=4.0, out_path=out,
            source_info=info, cfg=cfg, framing="track", crop_x=crop_x,
        ), log=log)
        png = work / (name + ".png")
        p = subprocess.run([
            ffmpeg_bin(), "-hide_banner", "-y", "-nostdin", "-ss", "1.0",
            "-i", str(out), "-frames:v", "1", str(png),
        ], capture_output=True, text=True)
        assert p.returncode == 0, p.stderr[-400:]
        return _blue_fraction(png)

    # The old top-left default kept the magenta block; the new default cuts it.
    with_left_crop = render("left.mp4", ([0.0, 3.0], [0.0, 10.0]))
    with_default = render("centered.mp4", None)
    assert with_left_crop > 0.005, with_left_crop
    assert with_default < with_left_crop * 0.2, (with_left_crop, with_default)
    print(f"PASS render default centres crop (marker {with_left_crop:.3f} -> {with_default:.3f})")


def main():
    test_local_never_returns_whole_frame()
    test_manual_whole_frame_is_upgraded()
    test_ai_whole_frame_is_rejected()
    test_webcam_face_cannot_steal_the_subject()
    test_character_face_beats_motion_noise()
    test_missing_track_still_centres_the_character()
    with tempfile.TemporaryDirectory(prefix="vcm_framingtest_") as tmp:
        test_render_centres_when_no_path(Path(tmp))
    print("GAME-SUBJECT FRAMING TEST PASSED")


if __name__ == "__main__":
    main()
