from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .util import LOG, clamp, read_json, write_json

# Haar cascades ship with opencv-python; robust enough for gameplay faces and
# very cheap on CPU. If a frame has no face we fall back to motion centroid so
# the crop still follows the action instead of guessing the frame centre.
_CASCADES = None
_YUNET = None
_YUNET_TRIED = False

YUNET_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx"
)


def _yunet(cfg):
    """YuNet is far better than Haar on stylised/game faces. Optional: downloaded once."""
    global _YUNET, _YUNET_TRIED
    if _YUNET_TRIED:
        return _YUNET
    _YUNET_TRIED = True
    try:
        import urllib.request

        import cv2

        try:  # OpenCV 5 logs a noisy DNN warning when the YuNet graph loads
            cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
        except Exception:  # noqa: BLE001
            pass

        model = cfg.models_dir / "face_detection_yunet_2023mar.onnx"
        if not model.exists() or model.stat().st_size < 100_000:
            LOG.info("downloading the YuNet face detector (230 KB, one time)")
            urllib.request.urlretrieve(YUNET_URL, model)
        _YUNET = cv2.FaceDetectorYN.create(str(model), "", (320, 320), 0.55, 0.3, 5000)
        LOG.info("face detector: YuNet")
    except Exception as exc:  # noqa: BLE001
        LOG.warning("YuNet face detector unavailable (%s); falling back to Haar/motion", exc)
        _YUNET = None
    return _YUNET


def _cascades():
    """Haar is only a backup: OpenCV 5 removed CascadeClassifier from the wheels."""
    global _CASCADES
    if _CASCADES is None:
        try:
            import cv2

            if not hasattr(cv2, "CascadeClassifier") or not hasattr(cv2, "data"):
                LOG.info("face detector: Haar unavailable in OpenCV %s", cv2.__version__)
                _CASCADES = []
            else:
                base = Path(cv2.data.haarcascades)
                _CASCADES = [
                    cv2.CascadeClassifier(str(base / "haarcascade_frontalface_default.xml")),
                    cv2.CascadeClassifier(str(base / "haarcascade_profileface.xml")),
                ]
                _CASCADES = [c for c in _CASCADES if not c.empty()]
        except Exception as exc:  # noqa: BLE001
            LOG.warning("Haar cascades unavailable (%s)", exc)
            _CASCADES = []
    return _CASCADES


@dataclass
class Sample:
    t: float
    face_x: float | None = None      # normalised centre 0..1
    face_y: float | None = None
    face_w: float | None = None      # normalised width
    face_conf: float = 0.0
    motion_x: float | None = None
    motion_y: float | None = None
    motion_conf: float = 0.0


def analyse_video(path: Path, cfg, cache_path: Path | None = None, sample_interval: float = 0.5, max_seconds: float = 0) -> dict:
    """Sample the video and return a face/motion track (cached per source)."""
    if cache_path and cache_path.exists():
        cached = read_json(cache_path)
        if (
            cached
            and cached.get("samples")
            and cached.get("sample_interval") == sample_interval
            and cached.get("mtime") == path.stat().st_mtime
        ):
            LOG.info("face track: using cache (%d samples)", len(cached["samples"]))
            return cached

    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV cannot open {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = max(1, int(round(fps * sample_interval)))

    cascades = _cascades()
    detector = _yunet(cfg)
    det_w = 640
    scale = det_w / max(width, 1)
    prev_gray_small = None
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
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            gray = cv2.equalizeHist(gray)
            sample = Sample(t=t)

            found = False
            if detector is not None:
                try:
                    detector.setInputSize((small.shape[1], small.shape[0]))
                    _, detections = detector.detect(small)
                except Exception:
                    detections = None
                if detections is not None and len(detections):
                    best = max(detections, key=lambda d: float(d[2]) * float(d[3]))
                    x, y, fw, fh = (float(v) for v in best[:4])
                    score = float(best[-1]) if len(best) >= 15 else 0.8
                    sample.face_x = (x + fw / 2) / small.shape[1]
                    sample.face_y = (y + fh / 2) / small.shape[0]
                    sample.face_w = fw / small.shape[1]
                    sample.face_conf = max(0.2, min(1.0, score))
                    found = True

            if not found:
                for cascade in cascades:
                    faces = cascade.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=5, minSize=(36, 36))
                    if len(faces):
                        x, y, fw, fh = max(faces, key=lambda f: int(f[2]) * int(f[3]))
                        sample.face_x = (x + fw / 2) / small.shape[1]
                        sample.face_y = (y + fh / 2) / small.shape[0]
                        sample.face_w = fw / small.shape[1]
                        sample.face_conf = min(1.0, (fw * fh) / (small.shape[1] * small.shape[0]) * 18.0)
                        break

            # Motion centroid: catches action even when no face is visible.
            if prev_gray_small is not None:
                diff = cv2.absdiff(gray, prev_gray_small)
                diff = cv2.GaussianBlur(diff, (9, 9), 0)
                mask = diff > max(12, float(diff.mean()) + 6)
                if mask.any():
                    ys, xs = np.nonzero(mask)
                    weights = diff[ys, xs].astype(np.float64)
                    total_w = float(weights.sum()) or 1.0
                    cx = float((xs * weights).sum() / total_w) / small.shape[1]
                    cy = float((ys * weights).sum() / total_w) / small.shape[0]
                    coverage = float(mask.mean())
                    sample.motion_x = clamp(cx, 0, 1)
                    sample.motion_y = clamp(cy, 0, 1)
                    sample.motion_conf = clamp(coverage * 8.0, 0.0, 1.0)
            prev_gray_small = gray
            samples.append(sample)
        index += 1

    cap.release()

    data = {
        "path": str(path),
        "mtime": path.stat().st_mtime,
        "fps": fps,
        "width": width,
        "height": height,
        "frame_count": total,
        "sample_interval": sample_interval,
        "samples": [s.__dict__ for s in samples],
    }
    if cache_path:
        write_json(cache_path, data)
    LOG.info("face track: analysed %d samples from %s", len(samples), path.name)
    return data


def _simplify_path(points: list[tuple[float, float]], epsilon: float, max_points: int = 80) -> list[tuple[float, float]]:
    """RDP, then loosen the tolerance until the path fits ffmpeg's expression budget."""
    simplified = rdp_points(points, epsilon)
    while len(simplified) > max_points and epsilon < 400:
        epsilon *= 1.6
        simplified = rdp_points(points, epsilon)
    return simplified


def _median(values: list[float], window: int = 5) -> list[float]:
    if not values:
        return values
    half = window // 2
    out = []
    for i in range(len(values)):
        chunk = values[max(0, i - half) : i + half + 1]
        out.append(float(sorted(chunk)[len(chunk) // 2]))
    return out


def _smooth_centered(values: list[float], radius: int = 2) -> list[float]:
    """Box blur centred on each sample - smooths without introducing tracking lag."""
    if not values:
        return values
    out = []
    for i in range(len(values)):
        lo = max(0, i - radius)
        hi = min(len(values), i + radius + 1)
        out.append(sum(values[lo:hi]) / (hi - lo))
    return out


def build_path(
    track: dict,
    start: float,
    end: float,
    crop_w: int,
    crop_h: int,
    cfg,
    max_gap: float = 2.0,
) -> dict:
    """Turn the sampled track into (time, x) / (time, y) pixel paths for ffmpeg."""
    src_w = track.get("width") or 1920
    src_h = track.get("height") or 1080
    samples = track.get("samples") or []
    if not samples:
        return {"x": ([0.0], [0.0]), "y": ([0.0], [0.0]), "face_coverage": 0.0, "motion_coverage": 0.0}

    interval = float(track.get("sample_interval", 0.5))
    inside = [s for s in samples if start - interval <= s["t"] <= end + interval]
    if not inside:
        inside = samples

    xs: list[float] = []
    ys: list[float] = []
    times: list[float] = []
    face_hits = 0
    motion_hits = 0
    last_face: tuple[float, float, float] | None = None   # x, y, timestamp
    last_motion: tuple[float, float] | None = None

    for s in inside:
        t = clamp(s["t"] - start, 0.0, end - start)
        cx: float | None = None
        cy: float | None = None
        if s.get("face_x") is not None and (s.get("face_conf") or 0) >= 0.05:
            cx, cy = s["face_x"], s.get("face_y") or 0.45
            face_hits += 1
            last_face = (cx, cy, s["t"])
        elif last_face is not None and (s["t"] - last_face[2]) <= max_gap:
            cx, cy = last_face[0], last_face[1]
        elif s.get("motion_x") is not None and (s.get("motion_conf") or 0) > 0.05:
            cx, cy = s["motion_x"], s.get("motion_y") or 0.5
            motion_hits += 1
            last_motion = (cx, cy)
        elif last_motion is not None:
            cx, cy = last_motion
        elif last_face is not None:
            cx, cy = last_face[0], last_face[1]
        else:
            cx, cy = 0.5, 0.5
        xs.append(clamp(cx * src_w - crop_w / 2, 0, max(0, src_w - crop_w)))
        ys.append(clamp(cy * src_h - crop_h / 2, 0, max(0, src_h - crop_h)))
        times.append(t)

    if len(times) < 2:
        times = [0.0, max(0.1, end - start)]
        xs = [xs[0] if xs else 0.0, xs[0] if xs else 0.0]
        ys = [ys[0] if ys else 0.0, ys[0] if ys else 0.0]

    # smooth (median removes detector flicker, centred averaging removes jitter without lag)
    xs = _median(xs, 3)
    ys = _median(ys, 3)
    xs = _smooth_centered(xs, radius=2)
    ys = _smooth_centered(ys, radius=2)

    # simplify the path so the ffmpeg expression stays small
    pad = float(cfg.get("video.crop_padding", 0.06)) * crop_w
    span = max(0.1, end - start)
    pts = [(times[i], xs[i]) for i in range(len(times))]
    simplified = _simplify_path(pts, epsilon=max(3.0, crop_w * 0.02))
    kt = [p[0] for p in simplified]
    kx = [clamp(p[1], pad if src_w - crop_w > 2 * pad else 0, max(0, src_w - crop_w - (pad if src_w - crop_w > 2 * pad else 0))) for p in simplified]
    if kt[0] > 0:
        kt.insert(0, 0.0)
        kx.insert(0, kx[0])
    if kt[-1] < span:
        kt.append(span)
        kx.append(kx[-1])

    ypts = [(times[i], ys[i]) for i in range(len(times))]
    ysimplified = _simplify_path(ypts, epsilon=max(3.0, crop_h * 0.02))
    yt = [p[0] for p in ysimplified]
    yv = [clamp(p[1], 0, max(0, src_h - crop_h)) for p in ysimplified]
    if yt[0] > 0:
        yt.insert(0, 0.0)
        yv.insert(0, yv[0])
    if yt[-1] < span:
        yt.append(span)
        yv.append(yv[-1])

    n = max(1, len(inside))
    return {
        "x": (kt, kx),
        "y": (yt, yv),
        "face_coverage": face_hits / n,
        "motion_coverage": motion_hits / n,
        "samples": n,
    }


def _last_time(samples: list[dict], current: dict) -> float:
    """Timestamp of the previous sample (kept for backwards compatibility)."""
    idx = samples.index(current)
    if idx <= 0:
        return current["t"]
    return samples[idx - 1]["t"]


def rdp_points(points: list[tuple[float, float]], epsilon: float = 4.0) -> list[tuple[float, float]]:
    """Simplify a (time, value) path. Uses vertical distance because time and pixels
    are different units - a euclidean distance would happily flatten a fast pan."""
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
        return rdp_points(points[: index + 1], epsilon)[:-1] + rdp_points(points[index:], epsilon)
    return [first, last]
