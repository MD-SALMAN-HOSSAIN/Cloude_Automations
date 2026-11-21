"""Regression test for the optional corner-facecam layout.

Checks three things:
1. find_facecam() distinguishes a corner webcam from a normal talking head.
2. build_path() ignores the webcam when following the real subject.
3. render_clip() really burns a circular bubble into the top corner.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import faces
from core.ffmpeg_tools import (ffmpeg_bin, ffprobe_bin, make_test_video, probe,
                               RenderRequest, render_clip)
from core.util import Logger

log = Logger(lambda level, msg: print(f"[{level}] {msg}"))


class FakeConfig:
    def __init__(self):
        self.data = {
            "video.width": 1080, "video.height": 1920, "video.fps": 30,
            "video.crf": 26, "video.preset": "veryfast",
            "video.facecam_size": 0.30, "video.crop_padding": 0.06,
            "features.normalize_audio": False,
            "video.loudness_lufs": -14.0, "video.true_peak_db": -1.5,
            "render.encoder": "cpu",
        }

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


def _track(x, y, w, h, n=20):
    return {
        "width": 1280, "height": 720, "sample_interval": 0.5,
        "samples": [{"t": i * 0.5, "face_x": x, "face_y": y, "face_w": w,
                     "face_h": h, "face_conf": 0.9,
                     "motion_x": 0.45, "motion_y": 0.55, "motion_conf": 0.6}
                    for i in range(n)],
    }


def test_detection():
    cam = faces.find_facecam(_track(0.84, 0.18, 0.08, 0.15))
    assert cam and cam["corner"] == "right", cam
    assert cam["coverage"] > 0.5, cam
    left = faces.find_facecam(_track(0.14, 0.20, 0.09, 0.16), corner="left")
    assert left and left["corner"] == "left", left
    assert faces.find_facecam(_track(0.50, 0.50, 0.28, 0.40)) is None
    assert faces.find_facecam(_track(0.84, 0.18, 0.45, 0.60)) is None
    print("PASS facecam detection (corner webcam vs talking head)")


def test_path_ignores_facecam():
    cfg = FakeConfig()
    track = _track(0.84, 0.18, 0.08, 0.15)
    rect = faces.find_facecam(track)
    crop_w, crop_h = 405, 720
    with_cam = faces.build_path(track, 0, 10, crop_w, crop_h, cfg,
                                focus_x=0.5, focus_y=0.45, ignore_rect=rect)
    plain = faces.build_path(track, 0, 10, crop_w, crop_h, cfg,
                             focus_x=0.5, focus_y=0.45)
    # Without exclusion the crop locks onto the webcam face at x~0.84*1280.
    assert plain["x"][1][0] > with_cam["x"][1][0] + 300, (
        plain["x"][1][0], with_cam["x"][1][0])
    assert with_cam["face_coverage"] == 0.0
    print(f"PASS crop ignores webcam (x {with_cam['x'][1][0]:.0f} vs {plain['x'][1][0]:.0f})")


def test_render(work: Path):
    src = make_test_video(work / "source.mp4", seconds=8)
    # A bright red "webcam" rectangle in the top-right of the source.
    drawn = work / "with_facecam.mp4"
    vf = "drawbox=x=900:y=60:w=200:h=200:color=red:t=fill"
    proc = subprocess.run([
        ffmpeg_bin(), "-hide_banner", "-y", "-nostdin", "-i", str(src),
        "-vf", vf, "-c:v", "libx264", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", "-c:a", "copy", str(drawn),
    ], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[-600:]

    info = probe(drawn)
    rect = {"x": 900 / 1280, "y": 60 / 720, "w": 200 / 1280,
            "h": 200 / 720, "corner": "right"}
    out = work / "out" / "clip.mp4"
    req = RenderRequest(source=drawn, start=1.0, end=6.0, out_path=out,
                        source_info=info, cfg=FakeConfig(),
                        framing="fit_blur", facecam=rect)
    result = render_clip(req, log=log)
    assert result["width"] == 1080 and result["height"] == 1920, result
    # Grab the frame and sample the centre of the bubble (top-right).
    png = work / "frame.png"
    p = subprocess.run([
        ffmpeg_bin(), "-hide_banner", "-y", "-nostdin", "-ss", "1.0",
        "-i", str(out), "-frames:v", "1", str(png),
    ], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-400:]
    import cv2
    frame = cv2.imread(str(png))
    assert frame is not None
    # Bubble is ~324px wide at x=1080-324-32=724, y=32 -> centre ~886,194.
    px = frame[194, 886]
    b, g, r = (int(v) for v in px)
    assert r > 120 and r > g * 1.6 and r > b * 1.6, (b, g, r)
    print(f"PASS rendered circle in top-right corner (centre RGB {r},{g},{b})")


def main():
    test_detection()
    test_path_ignores_facecam()
    with tempfile.TemporaryDirectory(prefix="vcm_facecamtest_") as tmp:
        test_render(Path(tmp))
    print("FACECAM TEST PASSED")


if __name__ == "__main__":
    main()
