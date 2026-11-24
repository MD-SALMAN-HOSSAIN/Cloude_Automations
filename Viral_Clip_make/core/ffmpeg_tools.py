from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .layout import split_boxes
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


def centered_crop_xy(width: int, height: int, crop_w: int, crop_h: int) -> tuple[int, int]:
    """Fallback crop origin that keeps the middle of the frame (the character)."""
    return max(0, (width - crop_w) // 2), max(0, (height - crop_h) // 2)


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


def facecam_layout(cfg, facecam: dict, width: int, height: int) -> dict:
    """Output-space size/position of the circular facecam bubble."""
    wanted = float(cfg.get("video.facecam_size", 0.30) or 0.30)
    size = int(clamp(width * wanted, min(140, width), min(width, height)))
    size -= size % 2
    margin = max(12, int(width * 0.03))
    x = margin if str(facecam.get("corner", "right")).lower() == "left" else width - size - margin
    y = margin
    return {"size": size, "x": int(x), "y": int(y), "margin": margin}


def _facecam_chains(facecam: dict, layout: dict, src_w: int, src_h: int,
                    duration: float, fps: int) -> list[str]:
    """FFmpeg chains: white ring + circular webcam crop over the main picture."""
    cw = max(2, int(float(facecam["w"]) * src_w))
    ch = max(2, int(float(facecam["h"]) * src_h))
    cw -= cw % 2
    ch -= ch % 2
    cx = int(clamp(float(facecam["x"]) * src_w, 0, max(0, src_w - cw)))
    cy = int(clamp(float(facecam["y"]) * src_h, 0, max(0, src_h - ch)))
    crop = f"crop=w={cw}:h={ch}:x={cx}:y={cy}"
    inner = int(layout["size"])
    ring = inner + 8
    alpha = "if(lte(hypot(X-W/2,Y-H/2),min(W,H)/2),255,0)"
    circle = (f"format=yuva420p,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='{alpha}'")
    x, y = int(layout["x"]), int(layout["y"])
    return [
        f"color=c=white:s={ring}x{ring}:r={fps}:d={duration:.3f},"
        f"{circle}[ring]",
        f"[fitv][ring]overlay=x={x - 4}:y={y - 4}:shortest=1[withring]",
        f"[cam]{crop},scale={inner}:{inner}:flags=lanczos,setsar=1,{circle}[bubble]",
        f"[withring][bubble]overlay=x={x}:y={y}:shortest=1[fcv]",
    ]


def _crop_xy_expr(req, info, crop_w: int, crop_h: int) -> tuple[str, str]:
    """Centred crop, overridden by the tracked path when it actually moves."""
    max_x, max_y = max(0, info.width - crop_w), max(0, info.height - crop_h)
    default_x, default_y = centered_crop_xy(info.width, info.height, crop_w, crop_h)
    x_expr = f"{default_x:.2f}" if max_x > 0 else "0"
    if req.crop_x and max_x > 0:
        times, values = req.crop_x
        values = [clamp(v, 0, max_x) for v in values]
        if max(values) - min(values) >= 1.5:
            x_expr = path_expression(times, values, "t")
    y_expr = f"{default_y:.2f}" if max_y > 0 else "0"
    if req.crop_y and max_y > 0:
        times, values = req.crop_y
        values = [clamp(v, 0, max_y) for v in values]
        if max(values) - min(values) >= 1.5:
            y_expr = path_expression(times, values, "t")
    return x_expr, y_expr


def _scale_into(box_w: int, box_h: int) -> str:
    """Centre-crop a source region so it fills the output box exactly."""
    return (f"scale={box_w}:{box_h}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={box_w}:{box_h},setsar=1")


def _split_layout_chains(req, info, cam: dict, boxes: dict, base: str,
                         fps: int, width: int, height: int) -> list[str]:
    """FFmpeg chains for facecam-on-top / gameplay-below with blurred edge bars."""
    fc = boxes["facecam"]
    gp = boxes["gameplay"]
    cw = max(2, int(float(cam["w"]) * info.width))
    ch = max(2, int(float(cam["h"]) * info.height))
    cw -= cw % 2
    ch -= ch % 2
    cx = int(clamp(float(cam["x"]) * info.width, 0, max(0, info.width - cw)))
    cy = int(clamp(float(cam["y"]) * info.height, 0, max(0, info.height - ch)))

    # Gameplay source crop follows the character; its shape is close to a square
    # / 4:3 window before being centre-cropped into the lower panel.
    src_cw, src_ch = crop_geometry(info.width, info.height, gp["w"], gp["h"])
    x_expr, y_expr = _crop_xy_expr(req, info, src_cw, src_ch)

    return [
        f"[0:v]{base},split=3[bg][fpsrc][gpsrc]",
        f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase:flags=bicubic,"
        f"crop={width}:{height},boxblur=18:2,setsar=1[bgfit]",
        f"[fpsrc]crop=w={cw}:h={ch}:x={cx}:y={cy},{_scale_into(fc['w'], fc['h'])}[fcfit]",
        f"[gpsrc]crop=w={src_cw}:h={src_ch}:x='{x_expr}':y='{y_expr}',"
        f"{_scale_into(gp['w'], gp['h'])}[gpfit]",
        f"[bgfit][fcfit]overlay=x={fc['x']}:y={fc['y']}:shortest=1[splitbg]",
        f"[splitbg][gpfit]overlay=x={gp['x']}:y={gp['y']}:shortest=1[fitv]",
    ]


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
    framing: str | None = None  # fit_blur | fit_black | track (overrides config)
    facecam: dict | None = None  # webcam/speaker crop rect from faces
    split_layout: bool = False   # facecam above, tracked gameplay below


def render_clip(req: RenderRequest, log=None) -> dict:
    cfg = req.cfg
    info = req.source_info or probe(req.source)
    width = int(cfg.get("video.width", 1080))
    height = int(cfg.get("video.height", 1920))
    fps = int(cfg.get("video.fps", 30))
    duration = max(0.5, req.end - req.start)

    req.out_path.parent.mkdir(parents=True, exist_ok=True)
    work = req.out_path.parent
    out_name = req.out_path.name

    framing = str(req.framing or cfg.get("video.framing", "fit_blur") or "fit_blur").lower()
    if framing not in ("fit_blur", "fit_black", "track"):
        framing = "fit_blur"
    trim_chain = [
        f"trim=start={req.start:.3f}:end={req.end:.3f}",
        "setpts=PTS-STARTPTS",
        f"fps={fps}",
    ]

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

    cap_name = ""
    if req.captions_ass is not None:
        cap_name = req.captions_ass.name.replace("\\", "/").replace(":", "\\:")

    cam = req.facecam
    boxes = split_boxes(cfg, width, height) if (req.split_layout and cam) else None
    split = bool(boxes)
    layout = facecam_layout(cfg, cam, width, height) if (cam and not split) else None
    chains: list[str] = []
    base = ",".join(trim_chain)
    fallback_base = None

    if split:
        chains = _split_layout_chains(req, info, cam, boxes, base, fps,
                                      width, height)
        # A 3-way graph is heavier: keep a plain character crop as a fallback.
        fcw, fch = crop_geometry(info.width, info.height, width, height)
        fcx, fcy = _crop_xy_expr(req, info, fcw, fch)
        fallback_base = (
            f"[0:v]{base},crop=w={fcw}:h={fch}:x='{fcx}':y='{fcy}',"
            f"scale={width}:{height}:flags=lanczos,setsar=1[fitv]"
        )
        if log:
            log.info("Layout: split screen - facecam top %.0f%%, gameplay bottom %.0f%%",
                     float(cfg.get("video.split_facecam_ratio", 0.33)) * 100,
                     float(cfg.get("video.split_gameplay_ratio", 0.57)) * 100)
    elif framing in ("fit_blur", "fit_black"):
        # Whole frame visible: scale it down to fit the vertical canvas and fill
        # the empty space (classic mobile gaming-shorts look). Nothing is cut.
        if framing == "fit_blur":
            labels = "[bg][fg][cam]" if cam else "[bg][fg]"
            split_count = 3 if cam else 2
        else:
            labels = "[bg][cam]" if cam else "[bg]"
            split_count = 2 if cam else 1
        # Every split pad must be consumed, otherwise FFmpeg auto-maps the
        # spare pad as an extra (wrong-sized) video stream.
        chains.append(f"[0:v]{base},split={split_count}{labels}")
        if framing == "fit_blur":
            chains.append(
                f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase:flags=bicubic,"
                f"crop={width}:{height},boxblur=18:2,setsar=1[bgfit]")
            chains.append(
                f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
                f"scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1[fgfit]")
            chains.append(
                f"[bgfit][fgfit]overlay=(W-w)/2:(H-h)/2:shortest=1[fitv]")
        else:
            chains.append(
                f"[bg]scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
                f"scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black[fitv]")
        if log:
            log.info("Framing: whole frame visible (%s background)",
                     "blurred" if framing == "fit_blur" else "black")
    else:
        # Subject-follow crop: fills the frame but necessarily cuts the sides.
        crop_w, crop_h = crop_geometry(info.width, info.height, width, height)
        x_expr, y_expr = _crop_xy_expr(req, info, crop_w, crop_h)
        crop_chain = [
            f"crop=w={crop_w}:h={crop_h}:x='{x_expr}':y='{y_expr}'",
            f"scale={width}:{height}:flags=lanczos",
            "setsar=1",
        ]
        if cam:
            chains.append(f"[0:v]{base},split=2[main][cam]")
            chains.append("[main]" + ",".join(crop_chain) + "[fitv]")
        else:
            chains.append("[0:v]" + ",".join(trim_chain + crop_chain) + "[fitv]")

    tail = (f";[fitv]subtitles=f={cap_name},format=yuv420p[v]" if cap_name
            else ";[fitv]format=yuv420p[v]")
    plain_graph = ";".join(chains) + tail
    if cam and not split:
        # Facecam last, so captions/hook stay on top of the bubble.
        chains.extend(_facecam_chains(cam, layout, info.width, info.height,
                                      duration, fps))
        if log:
            log.info("Facecam: circular webcam in top %s corner, subject centred",
                     cam.get("corner", "right"))
    video_tail = tail.replace("[fitv]", "[fcv]", 1) if (cam and not split) else tail
    graph = ";".join(chains) + video_tail

    if info.has_audio:
        audio_part = (
            f";[0:a]atrim=start={req.start:.3f}:end={req.end:.3f},asetpts=PTS-STARTPTS,"
            f"aresample=48000,{pre_chain},{normalise},alimiter=limit=0.95,aresample=48000[a]"
        )
        maps = ["-map", "[v]", "-map", "[a]"]
        audio_args = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    else:
        audio_part = (f";anullsrc=channel_layout=stereo:sample_rate=48000,"
                      f"atrim=0:{duration:.3f}[a]")
        maps = ["-map", "[v]", "-map", "[a]"]
        audio_args = ["-c:a", "aac", "-b:a", "128k", "-ar", "48000"]

    graph += audio_part
    plain_graph += audio_part
    fallback_graph = None
    if fallback_base:
        fallback_graph = fallback_base + tail + audio_part

    filter_file = work / "filter_graph.txt"
    filter_file.write_text(graph, encoding="utf-8")

    from .hardware import preferred_encoder

    crf = int(cfg.get("video.crf", 20) or 20)
    preset = str(cfg.get("video.preset", "medium") or "medium")
    encoder = preferred_encoder(cfg)
    bitrate = f"{max(3, int(round((40 - crf) * 0.6)))}M"

    def _video_args(enc: str) -> list[str]:
        if enc == "h264_nvenc":
            return ["-c:v", enc, "-preset", "p4", "-cq", str(crf), "-b:v", "0",
                    "-profile:v", "high"]
        if enc == "h264_qsv":
            return ["-c:v", enc, "-preset", "medium", "-b:v", bitrate]
        if enc == "h264_amf":
            return ["-c:v", enc, "-quality", "balanced", "-b:v", bitrate]
        return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf),
                "-profile:v", "high", "-level", "4.2"]

    out_file = work / out_name
    attempts = [encoder] if encoder == "libx264" else [encoder, "libx264"]
    fallback = fallback_graph or (plain_graph if cam else None)
    graphs = [graph, fallback] if fallback else [graph]
    used = "libx264"
    success = False
    error_tail = ""
    for variant, use_graph in enumerate(graphs):
        if variant:
            filter_file.write_text(use_graph, encoding="utf-8")
            if log:
                log.warn("Split/facecam layout failed - retrying with one gameplay frame")
        for attempt, enc in enumerate(attempts):
            if out_file.exists():
                try:
                    out_file.unlink()
                except OSError:
                    pass
            cmd = [
                ffmpeg_bin(), "-hide_banner", "-y", "-nostdin",
                "-i", str(req.source.resolve()),
                "-filter_complex", use_graph,
                *maps,
                *_video_args(enc),
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
            if proc.returncode == 0 and out_file.exists():
                used = enc
                success = True
                break
            error_tail = "\n".join((proc.stderr or "").strip().splitlines()[-10:])
            if attempt == 0:
                if log:
                    log.warn("Hardware encoder %s failed - retrying on CPU: %s",
                             enc, error_tail[-180:])
                continue
            break
        if success:
            break
    if not success:
        filter_file.unlink(missing_ok=True)
        raise RuntimeError(f"FFmpeg render failed:\n{error_tail}")

    if log:
        if used == "libx264" and encoder != "libx264":
            log.info("Video encoded on CPU (hardware encoder was unavailable)")
        else:
            log.info("Video encoded with %s", used)

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
