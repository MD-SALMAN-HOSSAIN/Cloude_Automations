from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .util import app_base_dir, clamp, run, which

_FFMPEG = None
_FFPROBE = None


def _bundled_bin(name: str) -> str | None:
    """Binaries copied next to the app (or bundled) take priority."""
    roots = [app_base_dir()]
    try:
        if getattr(__import__("sys"), "frozen", False):
            roots.append(Path(__import__("sys")._MEIPASS))
    except Exception:
        pass
    for root in roots:
        for candidate in (root / "bin" / name,
                          root / "_internal" / "bin" / name,
                          root / name):
            if candidate.exists():
                return str(candidate)
    return None


def ffmpeg_bin() -> str:
    global _FFMPEG
    if _FFMPEG is None:
        _FFMPEG = _bundled_bin("ffmpeg.exe") or _bundled_bin("ffmpeg") or which("ffmpeg")
        if not _FFMPEG:
            raise RuntimeError(
                "FFmpeg was not found. Install it with: winget install Gyan.FFmpeg\n"
                "or place ffmpeg.exe inside the app's 'bin' folder."
            )
    return _FFMPEG


def ffprobe_bin() -> str:
    global _FFPROBE
    if _FFPROBE is None:
        _FFPROBE = _bundled_bin("ffprobe.exe") or _bundled_bin("ffprobe") or which("ffprobe")
        if not _FFPROBE:
            raise RuntimeError("ffprobe was not found. Install FFmpeg (winget install Gyan.FFmpeg).")
    return _FFPROBE


@dataclass
class MediaInfo:
    path: Path
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool


def probe(path: Path | str) -> MediaInfo:
    path = Path(path)
    proc = run([
        ffprobe_bin(), "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ])
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path.name}: {(proc.stderr or '')[:300]}")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise RuntimeError(f"no video stream in {path.name}")

    duration = 0.0
    for source in (data.get("format", {}), video, audio or {}):
        try:
            duration = float(source.get("duration") or 0)
        except (TypeError, ValueError):
            duration = 0
        if duration > 0:
            break

    fps = 30.0
    try:
        num, den = (video.get("avg_frame_rate") or "30/1").split("/")
        fps = float(num) / float(den) if float(den) else 30.0
    except Exception:
        pass
    if not (1 <= fps <= 240):
        fps = 30.0

    rotation = 0
    try:
        for side in video.get("side_data_list", []) or []:
            if "rotation" in side:
                rotation = int(float(side["rotation"]))
    except Exception:
        rotation = 0

    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    if abs(rotation) % 180 == 90:
        width, height = height, width

    return MediaInfo(path=path, duration=duration, width=width, height=height,
                     fps=fps, has_audio=audio is not None)


# ---------------------------------------------------------------------------
# 9:16 crop maths
# ---------------------------------------------------------------------------

def crop_geometry(width: int, height: int, target_w: int = 1080, target_h: int = 1920) -> tuple[int, int]:
    ratio = target_w / target_h
    if width / max(height, 1) > ratio:
        crop_h = height
        crop_w = int(round(height * ratio))
    else:
        crop_w = width
        crop_h = int(round(width / ratio))
    crop_w -= crop_w % 2
    crop_h -= crop_h % 2
    return max(2, crop_w), max(2, crop_h)


def rdp_simplify(points: list[tuple[float, float]], epsilon: float = 3.0) -> list[tuple[float, float]]:
    if len(points) <= 2:
        return list(points)
    first, last = points[0], points[-1]
    span = last[0] - first[0]
    slope = (last[1] - first[1]) / span if span else 0.0
    max_dist, index = 0.0, 0
    for i in range(1, len(points) - 1):
        predicted = first[1] + slope * (points[i][0] - first[0])
        dist = abs(points[i][1] - predicted)
        if dist > max_dist:
            max_dist, index = dist, i
    if max_dist > epsilon:
        return rdp_simplify(points[: index + 1], epsilon)[:-1] + rdp_simplify(points[index:], epsilon)
    return [first, last]


def _segment(v0, v1, t0, t1, var):
    span = max(t1 - t0, 1e-4)
    slope = (v1 - v0) / span
    return f"({v0:.2f}+{slope:.3f}*({var}-{t0:.3f}))"


def path_expression(times: Sequence[float], values: Sequence[float], var: str = "t") -> str:
    if not times:
        return "0"
    if len(times) == 1 or max(values) - min(values) < 1.5:
        return f"{float(values[0]):.2f}"
    expr = f"{float(values[-1]):.2f}"
    for i in range(len(times) - 1, 0, -1):
        seg = _segment(float(values[i - 1]), float(values[i]), float(times[i - 1]), float(times[i]), var)
        expr = f"if(lt({var},{float(times[i]):.3f}),{seg},{expr})"
    return expr


# ---------------------------------------------------------------------------
# Audio loudness
# ---------------------------------------------------------------------------

@dataclass
class Loudness:
    input_i: float = -14.0
    input_tp: float = -1.5
    input_lra: float = 11.0
    input_thresh: float = -24.0
    target_offset: float = 0.0
    ok: bool = False


def measure_loudness(path: Path, start: float, end: float, pre_chain: str, cfg) -> Loudness:
    target_i = float(cfg.get("video.loudness_lufs", -14.0))
    target_tp = float(cfg.get("video.true_peak_db", -1.5))
    filt = f"atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS,{pre_chain}"
    filt += f",loudnorm=I={target_i}:TP={target_tp}:LRA=11:print_format=json"
    proc = run([
        ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(path),
        "-map", "0:a:0", "-af", filt, "-f", "null", "-",
    ], timeout=600)
    text = proc.stderr or ""
    start_idx, end_idx = text.rfind("{"), text.rfind("}")
    if start_idx == -1 or end_idx <= start_idx:
        return Loudness()
    try:
        block = json.loads(text[start_idx:end_idx + 1])
        values = {
            "input_i": float(block["input_i"]),
            "input_tp": float(block["input_tp"]),
            "input_lra": float(block["input_lra"]),
            "input_thresh": float(block["input_thresh"]),
            "target_offset": float(block.get("target_offset", 0) or 0),
        }
        if not all(math.isfinite(v) for v in values.values()):
            return Loudness()
        if not (-99.0 <= values["input_i"] <= 0.0):
            return Loudness()
        return Loudness(ok=True, **values)
    except Exception:
        return Loudness()


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

@dataclass
class RenderRequest:
    source: Path
    start: float
    end: float
    out_path: Path
    captions_ass: Path | None = None
    crop_x: tuple[list[float], list[float]] | None = None
    crop_y: tuple[list[float], list[float]] | None = None
    source_info: MediaInfo | None = None
    cfg: object = None
    quiet: bool = False


def render_clip(req: RenderRequest, log=None) -> dict:
    cfg = req.cfg
    info = req.source_info or probe(req.source)
    width = int(cfg.get("video.width", 1080))
    height = int(cfg.get("video.height", 1920))
    fps = int(cfg.get("video.fps", 30))
    duration = max(0.5, req.end - req.start)

    crop_w, crop_h = crop_geometry(info.width, info.height, width, height)
    max_x, max_y = max(0, info.width - crop_w), max(0, info.height - crop_h)
    req.out_path.parent.mkdir(parents=True, exist_ok=True)
    work = req.out_path.parent
    out_name = req.out_path.name

    x_expr = "0"
    if req.crop_x and max_x > 0:
        times, values = req.crop_x
        values = [clamp(v, 0, max_x) for v in values]
        if max(values) - min(values) >= 1.5:
            x_expr = path_expression(times, values, "t")
    y_expr = "0"
    if req.crop_y and max_y > 0:
        times, values = req.crop_y
        values = [clamp(v, 0, max_y) for v in values]
        if max(values) - min(values) >= 1.5:
            y_expr = path_expression(times, values, "t")

    pre_chain = "highpass=f=70,acompressor=threshold=-18dB:ratio=2:attack=5:release=150"
    target_i = float(cfg.get("video.loudness_lufs", -14.0))
    target_tp = float(cfg.get("video.true_peak_db", -1.5))
    loud = Loudness()
    if bool(cfg.get("features.normalize_audio", True)) and info.has_audio:
        loud = measure_loudness(req.source, req.start, req.end, pre_chain, cfg)
    if loud.ok:
        normalise = (
            f"loudnorm=I={target_i}:TP={target_tp}:LRA=11:linear=true"
            f":measured_I={loud.input_i}:measured_TP={loud.input_tp}"
            f":measured_LRA={loud.input_lra}:measured_thresh={loud.input_thresh}"
            f":offset={loud.target_offset}"
        )
    else:
        normalise = f"loudnorm=I={target_i}:TP={target_tp}:LRA=11"

    v_chain = [
        f"trim=start={req.start:.3f}:end={req.end:.3f}",
        "setpts=PTS-STARTPTS",
        f"fps={fps}",
        f"crop=w={crop_w}:h={crop_h}:x='{x_expr}':y='{y_expr}'",
        f"scale={width}:{height}:flags=lanczos",
        "setsar=1",
        "format=yuv420p",
    ]
    if req.captions_ass is not None:
        cap_name = req.captions_ass.name.replace("\\", "/").replace(":", "\\:")
        v_chain.append(f"subtitles=f={cap_name}")

    graph = "[0:v]" + ",".join(v_chain) + "[v]"
    if info.has_audio:
        graph += (
            f";[0:a]atrim=start={req.start:.3f}:end={req.end:.3f},asetpts=PTS-STARTPTS,"
            f"aresample=48000,{pre_chain},{normalise},alimiter=limit=0.95,aresample=48000[a]"
        )
        maps = ["-map", "[v]", "-map", "[a]"]
        audio_args = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    else:
        graph += f";anullsrc=channel_layout=stereo:sample_rate=48000,atrim=0:{duration:.3f}[a]"
        maps = ["-map", "[v]", "-map", "[a]"]
        audio_args = ["-c:a", "aac", "-b:a", "128k", "-ar", "48000"]

    filter_file = work / "filter_graph.txt"
    filter_file.write_text(graph, encoding="utf-8")

    cmd = [
        ffmpeg_bin(), "-hide_banner", "-y", "-nostdin",
        "-i", str(req.source.resolve()),
        "-filter_complex", graph,
        *maps,
        "-c:v", "libx264",
        "-preset", str(cfg.get("video.preset", "medium")),
        "-crf", str(cfg.get("video.crf", 20)),
        "-profile:v", "high", "-level", "4.2",
        "-g", str(fps * 2),
        "-pix_fmt", "yuv420p",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        *audio_args,
        "-movflags", "+faststart",
        "-shortest",
        "-t", f"{duration + 0.2:.3f}",
        out_name,
    ]
    proc = run(cmd, cwd=work, timeout=max(300, int(duration * 30)))
    if proc.returncode != 0 or not (work / out_name).exists():
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-12:])
        raise RuntimeError(f"FFmpeg render failed:\n{tail}")

    out = probe(req.out_path)
    try:
        filter_file.unlink(missing_ok=True)
    except Exception:
        pass
    return {
        "duration": out.duration,
        "width": out.width,
        "height": out.height,
        "size": req.out_path.stat().st_size,
    }


def make_thumbnail(video: Path, out: Path, at: float = 1.2, width: int = 1080) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = run([
        ffmpeg_bin(), "-hide_banner", "-y", "-nostdin",
        "-ss", f"{max(0, at):.2f}", "-i", str(video),
        "-frames:v", "1", "-vf", f"scale={max(2, int(width))}:-2", "-q:v", "2", str(out),
    ], timeout=120)
    return out


def make_test_video(path: Path, seconds: float = 24.0, size: str = "1280x720") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    proc = run([
        ffmpeg_bin(), "-hide_banner", "-y", "-nostdin",
        "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=320:duration={seconds}",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-shortest", str(path),
    ], timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(f"could not create test video: {(proc.stderr or '')[-300:]}")
    return path
