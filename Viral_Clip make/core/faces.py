from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .util import Logger, clamp

YUNET_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx"
)

_DETECTOR = None
_DETECTOR_TRIED = False


def _detector(cfg, log: Logger):
    """YuNet is a tiny (230 KB) free ONNX face detector; one-time download."""
    global _DETECTOR, _DETECTOR_TRIED
    if _DETECTOR_TRIED:
        return _DETECTOR
    _DETECTOR_TRIED = True
    try:
        import urllib.request

        import cv2
        try:
            cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
        except Exception:
            pass

        model = cfg.models_dir / "face_detection_yunet_2023mar.onnx"
        if not model.exists() or model.stat().st_size < 100_000:
            log.info("Downloading the tiny free face detector (230 KB, once)")
            urllib.request.urlretrieve(YUNET_URL, str(model))
        _DETECTOR = cv2.FaceDetectorYN.create(str(model), "", (320, 320), 0.55, 0.3, 5000)
        log.info("Face detector ready (YuNet)")
    except Exception as exc:
        log.warn("Face detector unavailable (%s); using motion tracking", exc)
        _DETECTOR = None
    return _DETECTOR


@dataclass
class Sample:
    t: float
    face_x: float | None = None
    face_y: float | None = None
    face_conf: float = 0.0
    motion_x: float | None = None
    motion_y: float | None = None
    motion_conf: float = 0.0


def analyse_video(path: Path, cfg, log: Logger, cache_path: Path | None = None,
                  sample_interval: float = 0.5, max_seconds: float = 0) -> dict:
    """Sample the video into a face/motion track (cached per source)."""
    if cache_path and cache_path.exists() and path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if (cached.get("samples") and cached.get("sample_interval") == sample_interval
                    and cached.get("mtime") == path.stat().st_mtime):
                log.info("Using cached subject track (%d samples)", len(cached["samples"]))
                return cached
        except Exception:
            pass

    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        log.warn("OpenCV could not open source for subject tracking")
        return {"samples": [], "width": 1920, "height": 1080, "sample_interval": sample_interval}
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = max(1, int(round(fps * sample_interval)))

    detector = _detector(cfg, log)
    det_w = 640
    scale = det_w / max(width, 1)
    prev_gray = None
    samples: list[Sample] = []
    index = 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if index % step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            t = index / fps
            if max_seconds and t > max_seconds:
                break
            small = cv2.resize(frame, (det_w, int(height * scale)))
            gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
            sample = Sample(t=t)

            if detector is not None:
                try:
                    detector.setInputSize((small.shape[1], small.shape[0]))
                    _, detections = detector.detect(small)
                except Exception:
                    detections = None
                if detections is not None and len(detections):
                    best = max(detections, key=lambda d: float(d[2]) * float(d[3]))
                    x, y, fw, fh = (float(v) for v in best[:4])
                    conf = float(best[-1]) if len(best) >= 15 else 0.8
                    sample.face_x = (x + fw / 2) / small.shape[1]
                    sample.face_y = (y + fh / 2) / small.shape[0]
                    sample.face_conf = max(0.2, min(1.0, conf))

            # Motion centroid: follows the action when no face is visible.
            if prev_gray is not None:
                diff = cv2.GaussianBlur(cv2.absdiff(gray, prev_gray), (9, 9), 0)
                mask = diff > max(12, float(diff.mean()) + 6)
                if mask.any():
                    ys, xs = np.nonzero(mask)
                    weights = diff[ys, xs].astype(np.float64)
                    total_w = float(weights.sum()) or 1.0
                    sample.motion_x = clamp(float((xs * weights).sum() / total_w) / small.shape[1], 0, 1)
                    sample.motion_y = clamp(float((ys * weights).sum() / total_w) / small.shape[0], 0, 1)
                    sample.motion_conf = clamp(float(mask.mean()) * 8.0, 0, 1)
            prev_gray = gray
            samples.append(sample)
        index += 1

    cap.release()
    data = {
        "path": str(path),
        "mtime": path.stat().st_mtime if path.exists() else 0,
        "fps": fps,
        "width": width,
        "height": height,
        "frame_count": total,
        "sample_interval": sample_interval,
        "samples": [s.__dict__ for s in samples],
    }
    if cache_path:
        try:
            cache_path.write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass
    log.info("Subject track analysed (%d samples)", len(samples))
    return data


def _median(values, window=3):
    half = window // 2
    return [float(sorted(values[max(0, i - half):i + half + 1])[len(values[max(0, i - half):i + half + 1]) // 2])
            for i in range(len(values))] if values else values


def _smooth(values, radius=2):
    if not values:
        return values
    out = []
    for i in range(len(values)):
        lo, hi = max(0, i - radius), min(len(values), i + radius + 1)
        out.append(sum(values[lo:hi]) / (hi - lo))
    return out


def _rdp(points, epsilon):
    if len(points) <= 2:
        return list(points)
    first, last = points[0], points[-1]
    span = last[0] - first[0]
    slope = (last[1] - first[1]) / span if span else 0.0
    max_dist, index = 0.0, 0
    for i in range(1, len(points) - 1):
        pred = first[1] + slope * (points[i][0] - first[0])
        dist = abs(points[i][1] - pred)
        if dist > max_dist:
            max_dist, index = dist, i
    if max_dist > epsilon:
        return _rdp(points[:index + 1], epsilon)[:-1] + _rdp(points[index:], epsilon)
    return [first, last]


def _simplify(points, epsilon, max_points=80):
    result = _rdp(points, epsilon)
    while len(result) > max_points and epsilon < 400:
        epsilon *= 1.6
        result = _rdp(points, epsilon)
    return result


def build_path(track: dict, start: float, end: float, crop_w: int, crop_h: int, cfg) -> dict:
    src_w = track.get("width") or 1920
    src_h = track.get("height") or 1080
    samples = track.get("samples") or []
    if not samples:
        return {"x": ([0.0], [0.0]), "y": ([0.0], [0.0]), "face_coverage": 0.0}

    interval = float(track.get("sample_interval", 0.5))
    inside = [s for s in samples if start - interval <= s["t"] <= end + interval] or samples
    xs, ys, times = [], [], []
    face_hits = 0
    last_face = None
    last_motion = None
    for s in inside:
        t = clamp(s["t"] - start, 0.0, end - start)
        cx = cy = None
        if s.get("face_x") is not None and (s.get("face_conf") or 0) >= 0.05:
            cx, cy = s["face_x"], s.get("face_y") or 0.45
            face_hits += 1
            last_face = (cx, cy)
        elif last_face is not None:
            cx, cy = last_face
        elif s.get("motion_x") is not None and (s.get("motion_conf") or 0) > 0.05:
            cx, cy = s["motion_x"], s["motion_y"]
            last_motion = (cx, cy)
        elif last_motion is not None:
            cx, cy = last_motion
        else:
            cx, cy = 0.5, 0.5
        xs.append(clamp(cx * src_w - crop_w / 2, 0, max(0, src_w - crop_w)))
        ys.append(clamp(cy * src_h - crop_h / 2, 0, max(0, src_h - crop_h)))
        times.append(t)

    if len(times) < 2:
        times = [0.0, max(0.1, end - start)]
        xs = [xs[0] if xs else 0.0] * 2
        ys = [ys[0] if ys else 0.0] * 2

    xs, ys = _smooth(_median(xs, 3)), _smooth(_median(ys, 3))
    span = max(0.1, end - start)
    xpts = _simplify([(times[i], xs[i]) for i in range(len(times))], max(3.0, crop_w * 0.02))
    ypts = _simplify([(times[i], ys[i]) for i in range(len(times))], max(3.0, crop_h * 0.02))
    kt, kx = [p[0] for p in xpts], [clamp(p[1], 0, max(0, src_w - crop_w)) for p in xpts]
    yt, yv = [p[0] for p in ypts], [clamp(p[1], 0, max(0, src_h - crop_h)) for p in ypts]
    if kt and kt[0] > 0:
        kt.insert(0, 0.0); kx.insert(0, kx[0])
    if kt and kt[-1] < span:
        kt.append(span); kx.append(kx[-1])
    if yt and yt[0] > 0:
        yt.insert(0, 0.0); yv.insert(0, yv[0])
    if yt and yt[-1] < span:
        yt.append(span); yv.append(yv[-1])

    return {
        "x": (kt, kx), "y": (yt, yv),
        "face_coverage": face_hits / max(1, len(inside)),
    }
