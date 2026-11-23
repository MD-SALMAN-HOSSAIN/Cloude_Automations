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
    face_w: float = 0.0
    face_h: float = 0.0
    face_conf: float = 0.0
    motion_x: float | None = None
    motion_y: float | None = None
    motion_conf: float = 0.0
    # The inferred game character / centre-of-action. This is what the crop
    # follows, not a raw face from a webcam bubble or a HUD element.
    subject_x: float | None = None
    subject_y: float | None = None
    subject_conf: float = 0.0
    subject_kind: str = "center"


def _game_motion_point(diff, width: int, height: int):
    """Centroid of the largest motion blob near the middle of the frame.

    A raw weighted average of *every* changed pixel drifts toward HUDs, minimaps
    and scrolling backgrounds. Picking the dominant blob and pulling it gently
    toward the centre keeps the crop on the game character, which is the whole
    point of the game-subject lock.
    """
    mask = (diff > max(12, float(diff.mean()) + 6)).astype(np.uint8)
    bx, by = max(2, int(width * 0.06)), max(2, int(height * 0.06))
    mask[:by, :] = 0
    mask[height - by:, :] = 0
    mask[:, :bx] = 0
    mask[:, width - bx:] = 0
    if not mask.any():
        return None

    import cv2

    count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    if count <= 1:
        return None
    cx0, cy0 = width / 2.0, height / 2.0
    best = None
    for i in range(1, count):
        area = float(stats[i, cv2.CC_STAT_AREA])
        if area < 12:
            continue
        px, py = centroids[i]
        # Big blobs win; a blob near the middle wins a tie against a HUD edge.
        score = area / (1.0 + 0.5 * (abs(px - cx0) / width + abs(py - cy0) / height))
        if best is None or score > best[0]:
            best = (score, i)
    if best is None:
        return None

    ys, xs = np.nonzero(labels == best[1])
    weights = diff[ys, xs].astype(np.float64)
    total = float(weights.sum()) or 1.0
    mx = clamp(float((xs * weights).sum() / total) / max(width, 1), 0, 1)
    my = clamp(float((ys * weights).sum() / total) / max(height, 1), 0, 1)
    # Small centre bias: character dominates even when an effect pulls the blob.
    mx = clamp(mx * 0.82 + 0.5 * 0.18, 0, 1)
    my = clamp(my * 0.82 + 0.45 * 0.18, 0, 1)
    area = float(stats[best[1], cv2.CC_STAT_AREA])
    conf = clamp(area / float(width * height) * 14.0, 0, 1)
    return mx, my, conf


def analyse_video(path: Path, cfg, log: Logger, cache_path: Path | None = None,
                  sample_interval: float = 0.5, max_seconds: float = 0) -> dict:
    """Sample the video into a face/motion track (cached per source)."""
    if cache_path and cache_path.exists() and path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if (cached.get("version") == 3 and cached.get("samples")
                    and cached.get("sample_interval") == sample_interval
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
                    sample.face_w = fw / small.shape[1]
                    sample.face_h = fh / small.shape[0]
                    sample.face_conf = max(0.2, min(1.0, conf))

            # Motion: the dominant centre-of-action blob (the game character).
            if prev_gray is not None:
                diff = cv2.GaussianBlur(cv2.absdiff(gray, prev_gray), (9, 9), 0)
                point = _game_motion_point(diff, small.shape[1], small.shape[0])
                if point:
                    sample.motion_x, sample.motion_y, sample.motion_conf = point
                    sample.subject_x, sample.subject_y = point[0], point[1]
                    sample.subject_conf = point[2]
                    sample.subject_kind = "motion"
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
        "version": 3,  # v3 adds the centre-of-action game-subject track
        "samples": [s.__dict__ for s in samples],
    }
    if cache_path:
        try:
            cache_path.write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass
    log.info("Subject track analysed (%d samples)", len(samples))
    return data


def _median_num(values):
    return float(sorted(values)[len(values) // 2]) if values else 0.0


def find_facecam(track: dict, corner: str = "auto") -> dict | None:
    """Find a small, persistent webcam bubble in a corner of the source video.

    A talking-head clip has one big face near the middle - that is the subject,
    not a facecam. A facecam is a *small* face that stays in one corner while
    the rest of the frame is the real content. Returns a normalised crop rect
    in source coordinates, or None when no facecam is found.
    """
    samples = track.get("samples") or []
    if len(samples) < 4:
        return None
    src_w = float(track.get("width") or 1920)
    src_h = float(track.get("height") or 1080)
    faces = [s for s in samples
             if s.get("face_x") is not None and (s.get("face_conf") or 0) >= 0.20
             and (s.get("face_w") or 0) > 0 and (s.get("face_h") or 0) > 0]
    if len(faces) < max(3, int(len(samples) * 0.15)):
        return None

    # Small face, near a corner / upper part of the frame.
    candidates = []
    for s in faces:
        x, y = float(s["face_x"]), float(s.get("face_y") or 0.45)
        w, h = float(s["face_w"]), float(s["face_h"])
        if (x < 0.34 or x > 0.66) and y < 0.38 and w < 0.34 and h < 0.55:
            candidates.append((float(s.get("t", 0.0)), x, y, w, h))
    if len(candidates) < max(3, int(len(samples) * 0.10)):
        return None
    if len(candidates) < len(faces) * 0.45:
        return None

    # The bubble must be there throughout the clip, not just in one moment.
    times = [float(s.get("t", 0.0)) for s in samples]
    mid = (max(times) / 2.0) if times else 0.0
    if not any(t <= mid for t, *_ in candidates) or not any(t > mid for t, *_ in candidates):
        return None

    cx = _median_num([c[1] for c in candidates])
    cy = _median_num([c[2] for c in candidates])
    # A real webcam stays put. Scattered one-off detections (game characters,
    # thumbnails) must not be mistaken for a facecam.
    near = [c for c in candidates
            if abs(c[1] - cx) <= 0.18 and abs(c[2] - cy) <= 0.22]
    if len(near) < len(candidates) * 0.70:
        return None
    cx = _median_num([c[1] for c in near])
    cy = _median_num([c[2] for c in near])
    if cy > 0.34:  # a facecam lives near the top, not beside the captions
        return None
    face_px = _median_num([c[3] for c in near]) * src_w
    face_py = _median_num([c[4] for c in near]) * src_h
    side = max(face_px, face_py) * 1.7  # face + hair/shoulders
    limit = min(src_w, src_h)
    side = clamp(side, limit * 0.14, limit * 0.46)
    x0 = clamp(cx * src_w - side / 2, 0, max(0, src_w - side))
    y0 = clamp(cy * src_h - side / 2, 0, max(0, src_h - side))
    chosen = str(corner or "auto").lower()
    side_name = chosen if chosen in ("left", "right") else ("left" if cx < 0.5 else "right")
    return {
        "x": x0 / src_w, "y": y0 / src_h,
        "w": side / src_w, "h": side / src_h,
        "corner": side_name, "cx": cx, "cy": cy,
        "coverage": round(len(candidates) / len(samples), 3),
    }


def _inside(px: float, py: float, rect: dict | None) -> bool:
    return bool(rect and rect["x"] <= px <= rect["x"] + rect["w"]
                and rect["y"] <= py <= rect["y"] + rect["h"])


def subject_point(sample: dict, ignore_rect: dict | None = None):
    """Pick the game character for one sample.

    Priority: a detected face that is NOT the webcam bubble (in gameplay this is
    the character), then the centre-of-action motion blob, then the middle of
    the frame. A real person's facecam must never steal the crop.
    """
    fx, fy = sample.get("face_x"), sample.get("face_y")
    if (fx is not None and (sample.get("face_conf") or 0) >= 0.12
            and not _inside(float(fx), float(fy or 0.45), ignore_rect)):
        return float(fx), float(fy or 0.45), "character", float(sample.get("face_conf") or 0.5)
    sx, sy = sample.get("subject_x"), sample.get("subject_y")
    if (sx is not None and sy is not None and (sample.get("subject_conf") or 0) > 0.05):
        return float(sx), float(sy), str(sample.get("subject_kind") or "motion"), \
            float(sample.get("subject_conf") or 0.5)
    mx, my = sample.get("motion_x"), sample.get("motion_y")
    if (mx is not None and my is not None and (sample.get("motion_conf") or 0) > 0.05):
        return float(mx), float(my), "motion", float(sample.get("motion_conf") or 0.5)
    return 0.5, 0.45, "center", 0.0


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


def build_path(track: dict, start: float, end: float, crop_w: int, crop_h: int, cfg,
               focus_x: float = 0.5, focus_y: float = 0.42,
               ignore_rect: dict | None = None) -> dict:
    """Build a smoothed crop path that keeps the subject at the chosen focus point.

    focus_y defaults to slightly above centre so word captions along the bottom
    of a Short never cover the subject's face. ``ignore_rect`` excludes a
    corner facecam bubble so the crop follows the real content, not the host.
    """
    src_w = track.get("width") or 1920
    src_h = track.get("height") or 1080
    samples = track.get("samples") or []
    if not samples:
        # No tracking data must still produce a centred crop. A top-left crop
        # would push the character off frame - the opposite of the subject lock.
        cx0 = max(0.0, (src_w - crop_w) / 2.0)
        cy0 = max(0.0, (src_h - crop_h) / 2.0)
        span = max(0.1, end - start)
        return {"x": ([0.0, span], [cx0, cx0]), "y": ([0.0, span], [cy0, cy0]),
                "face_coverage": 0.0, "focus_x": 0.5, "focus_y": 0.45,
                "subject": "center"}

    max_x, max_y = max(0, src_w - crop_w), max(0, src_h - crop_h)
    fx = clamp(focus_x, 0.25, 0.75)
    fy = clamp(focus_y, 0.25, 0.65)
    pad = clamp(float(cfg.get("video.crop_padding", 0.06) or 0), 0, 0.30)

    interval = float(track.get("sample_interval", 0.5))
    inside = [s for s in samples if start - interval <= s["t"] <= end + interval] or samples
    xs, ys, times = [], [], []
    face_hits = 0
    last_subject = None
    kinds: list[str] = []
    for s in inside:
        t = clamp(s["t"] - start, 0.0, end - start)
        cx, cy, kind, conf = subject_point(s, ignore_rect)
        if conf > 0.05:
            last_subject = (cx, cy)
            kinds.append(kind)
            if kind == "character":
                face_hits += 1
        elif last_subject is not None:
            cx, cy = last_subject
        else:
            cx, cy = 0.5, 0.45

        sx, sy = cx * src_w, cy * src_h
        x = clamp(sx - crop_w * fx, 0, max_x)
        y = clamp(sy - crop_h * fy, 0, max_y)
        # Keep a margin around the subject where the source has room for it.
        local_x = (sx - x) / max(1, crop_w)
        if local_x < pad and x > 0:
            x = clamp(sx - crop_w * pad, 0, max_x)
        elif local_x > 1 - pad and x < max_x:
            x = clamp(sx - crop_w * (1 - pad), 0, max_x)
        local_y = (sy - y) / max(1, crop_h)
        if local_y < pad and y > 0:
            y = clamp(sy - crop_h * pad, 0, max_y)
        elif local_y > 1 - pad and y < max_y:
            y = clamp(sy - crop_h * (1 - pad), 0, max_y)
        xs.append(x)
        ys.append(y)
        times.append(t)

    if len(times) < 2:
        times = [0.0, max(0.1, end - start)]
        xs = [xs[0] if xs else 0.0] * 2
        ys = [ys[0] if ys else 0.0] * 2

    xs, ys = _smooth(_median(xs, 3)), _smooth(_median(ys, 3))
    span = max(0.1, end - start)
    xpts = _simplify([(times[i], xs[i]) for i in range(len(times))], max(3.0, crop_w * 0.02))
    ypts = _simplify([(times[i], ys[i]) for i in range(len(times))], max(3.0, crop_h * 0.02))
    kt, kx = [p[0] for p in xpts], [clamp(p[1], 0, max_x) for p in xpts]
    yt, yv = [p[0] for p in ypts], [clamp(p[1], 0, max_y) for p in ypts]
    if kt and kt[0] > 0:
        kt.insert(0, 0.0); kx.insert(0, kx[0])
    if kt and kt[-1] < span:
        kt.append(span); kx.append(kx[-1])
    if yt and yt[0] > 0:
        yt.insert(0, 0.0); yv.insert(0, yv[0])
    if yt and yt[-1] < span:
        yt.append(span); yv.append(yv[-1])

    dominant = max(set(kinds), key=kinds.count) if kinds else "center"
    return {
        "x": (kt, kx), "y": (yt, yv),
        "face_coverage": face_hits / max(1, len(inside)),
        "focus_x": fx, "focus_y": fy,
        "subject": dominant,
    }
