"""Regression test for the split-screen gaming layout.

Checks that:
1. The geometry keeps the facecam at the top, gameplay below and safe bars.
2. A persistent top-of-frame face is detected as the speaker.
3. Captions use the split styles and burn in the channel handle.
4. render_clip() actually renders a 1080x1920 two-panel clip.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import faces
from core.captions import build_ass, write_ass
from core.config import default_settings
from core.ffmpeg_tools import (ffmpeg_bin, make_test_video, probe, render_clip,
                               RenderRequest)
from core.layout import split_boxes
from core.util import Logger

log = Logger(lambda level, msg: print(f"[{level}] {msg}"))


def cfg_for_split() -> dict:
    cfg = default_settings()
    cfg.update({
        "video.width": 1080, "video.height": 1920,
        "features.split_layout": True,
        "style.watermark": "twitch.tv/test",
        "video.crf": 28, "video.preset": "ultrafast",
    })
    return cfg


def test_geometry():
    boxes = split_boxes(cfg_for_split(), 1080, 1920)
    fc, gp = boxes["facecam"], boxes["gameplay"]
    assert fc["y"] < gp["y"], boxes
    assert gp["y"] + gp["h"] <= 1920, boxes
    assert 0.28 <= fc["h"] / 1920 <= 0.36, boxes
    assert 0.53 <= gp["h"] / 1920 <= 0.60, boxes
    assert boxes["top_bar"] >= 20 and boxes["bottom_bar"] >= 40, boxes
    print("PASS split geometry (facecam top, gameplay bottom, safe bars)")


def _track(x, y, w, h, n=20):
    return {
        "width": 1280, "height": 720, "sample_interval": 0.5,
        "samples": [{"t": i * 0.5, "face_x": x, "face_y": y, "face_w": w,
                     "face_h": h, "face_conf": 0.9,
                     "motion_x": 0.45, "motion_y": 0.55, "motion_conf": 0.6}
                    for i in range(n)],
    }


def test_speaker_detection():
    top = faces.find_speaker_facecam(_track(0.50, 0.22, 0.10, 0.18))
    assert top and top["corner"] == "top", top
    corner = faces.find_speaker_facecam(_track(0.84, 0.18, 0.08, 0.15))
    assert corner and corner["corner"] == "right", corner
    assert faces.find_speaker_facecam({"samples": [], "width": 1280,
                                       "height": 720}) is None
    print("PASS speaker detection (top face and corner webcam)")


def test_captions(work: Path):
    cfg = cfg_for_split()
    words = [
        type("W", (), {"start": 0.2, "end": 0.6, "text": "THIS"})(),
        type("W", (), {"start": 0.65, "end": 1.1, "text": "IS"})(),
        type("W", (), {"start": 1.15, "end": 1.8, "text": "HUGE"})(),
    ]
    ass = work / "caps.ass"
    write_ass(ass, build_ass(words, "HUGE REACTION", 3.0, cfg))
    text = ass.read_text(encoding="utf-8")
    assert "Style: SeamCap," in text, text
    assert "twitch.tv/test" in text, text
    assert ",SeamCap," in text, text
    assert "\\c&H0000E5FF" in text, text
    print("PASS split captions (seam style, yellow highlight, handle)")


def test_render(work: Path):
    src = make_test_video(work / "source.mp4", seconds=6)
    cfg = cfg_for_split()
    out = work / "out" / "clip.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    ass = out.parent / "caps.ass"
    write_ass(ass, build_ass([], "HUGE REACTION", 5.0, cfg))
    rect = {"x": 0.10, "y": 0.05, "w": 0.22, "h": 0.30, "corner": "top"}
    req = RenderRequest(
        source=src, start=1.0, end=5.0, out_path=out,
        captions_ass=ass, source_info=probe(src), cfg=cfg,
        framing="track", facecam=rect, split_layout=True,
    )
    result = render_clip(req, log=log)
    assert result["width"] == 1080 and result["height"] == 1920, result
    frame = work / "frame.png"
    p = subprocess.run([
        ffmpeg_bin(), "-hide_banner", "-y", "-nostdin", "-ss", "1.0",
        "-i", str(out), "-frames:v", "1", str(frame),
    ], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-400:]
    import cv2
    image = cv2.imread(str(frame))
    assert image is not None and image.shape[:2] == (1920, 1080), image.shape
    print("PASS split render (1080x1920, two panels)")


def main():
    test_geometry()
    test_speaker_detection()
    with tempfile.TemporaryDirectory(prefix="vcm_splittest_") as tmp:
        work = Path(tmp)
        test_captions(work)
        test_render(work)
    print("SPLIT TEST PASSED")


if __name__ == "__main__":
    main()
