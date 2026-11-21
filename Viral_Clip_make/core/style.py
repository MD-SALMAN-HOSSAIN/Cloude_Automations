"""AI-assisted clip style selection and subject-aware framing.

The style engine answers one question for every moment: how should this moment
be fitted into a vertical short so the subject stays clearly visible?

Modes (``video.framing``):
  auto      - AI picks the best style per clip (local heuristic if no API key)
  track     - crop to the target ratio and follow the face / action
  fit_blur  - show the whole frame on a blurred vertical canvas
  fit_black - show the whole frame on black bars

The local heuristic is the free default and never needs a key. When an AI
provider is configured, the same choices are sent to the model together with
measured subject signals (face coverage, motion, subject position) so the
model can pick the right look for each moment.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from .util import Logger, clamp

FRAMINGS = ("auto", "track", "fit_blur", "fit_black")

STYLE_LABELS = {
    "auto": "AI picks best",
    "track": "Follow subject (crop)",
    "fit_blur": "Whole frame (blurred)",
    "fit_black": "Whole frame (black bars)",
}


@dataclass
class StyleDecision:
    framing: str = "auto"
    focus_x: float = 0.5      # where the subject sits horizontally in the crop
    focus_y: float = 0.42     # slightly above centre: captions use the bottom
    reason: str = ""
    confidence: float = 0.0
    source: str = "local"     # ai | local | manual

    def as_dict(self) -> dict:
        return asdict(self)


def _in_rect(px: float, py: float, rect: dict | None) -> bool:
    return bool(rect and rect["x"] <= px <= rect["x"] + rect["w"]
                and rect["y"] <= py <= rect["y"] + rect["h"])


def _empty_metrics() -> dict:
    return {
        "face_coverage": 0.0, "motion_coverage": 0.0,
        "cx": 0.5, "cy": 0.5, "spread_x": 0.0, "spread_y": 0.0,
        "edge_hits": 0.0, "samples": 0,
        "source_ratio": 16 / 9, "target_ratio": 9 / 16,
    }


def moment_metrics(track: dict | None, moment, cfg, info=None,
                   ignore_rect: dict | None = None) -> dict:
    """Measure how a moment should be framed, from the cached subject track."""
    result = _empty_metrics()
    if info is not None:
        result["source_ratio"] = round(float(info.width) / max(1, info.height), 3)
        result["target_ratio"] = round(
            float(cfg.get("video.width", 1080)) / max(1, int(cfg.get("video.height", 1920))), 3
        )
    if not track:
        return result

    samples = track.get("samples") or []
    if not samples:
        return result
    interval = float(track.get("sample_interval", 0.5) or 0.5)
    inside = [s for s in samples
              if moment.start - interval <= float(s.get("t", 0)) <= moment.end + interval]
    if not inside:
        inside = samples[-20:]
    if not inside:
        return result

    faces, motions, all_x, all_y = [], [], [], []
    for s in inside:
        fx, fy = s.get("face_x"), float(s.get("face_y") or 0.45)
        if (fx is not None and (s.get("face_conf") or 0) >= 0.05
                and not _in_rect(float(fx), fy, ignore_rect)):
            p = (float(fx), fy)
            faces.append(p)
            all_x.append(p[0]); all_y.append(p[1])
        elif s.get("motion_x") is not None and (s.get("motion_conf") or 0) > 0.05:
            p = (float(s["motion_x"]), float(s.get("motion_y") or 0.5))
            motions.append(p)
            all_x.append(p[0]); all_y.append(p[1])

    points = faces or motions
    n = len(inside)
    result["samples"] = n
    result["face_coverage"] = round(len(faces) / n, 3)
    result["motion_coverage"] = round(len(motions) / n, 3)
    if points:
        result["cx"] = round(sum(p[0] for p in points) / len(points), 3)
        result["cy"] = round(sum(p[1] for p in points) / len(points), 3)
        result["spread_x"] = round(max(p[0] for p in points) - min(p[0] for p in points), 3)
        result["spread_y"] = round(max(p[1] for p in points) - min(p[1] for p in points), 3)
        result["edge_hits"] = round(
            sum(1 for p in points if p[0] < 0.2 or p[0] > 0.8 or p[1] < 0.12 or p[1] > 0.88)
            / len(points), 3
        )
    return result


def _focus_from(metrics: dict, cfg) -> tuple[float, float]:
    x = clamp(float(metrics.get("cx", 0.5)), 0.32, 0.68)
    y = clamp(float(cfg.get("video.subject_focus_y", 0.42) or 0.42), 0.30, 0.58)
    # A subject already low in the frame would be hidden by captions; raise it.
    if float(metrics.get("cy", 0.5)) > 0.62:
        y = min(y, 0.38)
    return round(x, 3), round(y, 3)


def local_decide(metrics: dict, cfg) -> StyleDecision:
    """Free, deterministic style pick based on measured subject signals."""
    focus_x, focus_y = _focus_from(metrics, cfg)
    ratio_gap = abs(float(metrics.get("source_ratio", 16 / 9))
                    - float(metrics.get("target_ratio", 9 / 16)))
    face = float(metrics.get("face_coverage", 0))
    motion = float(metrics.get("motion_coverage", 0))
    spread = float(metrics.get("spread_x", 0))
    edge = float(metrics.get("edge_hits", 0))

    if ratio_gap < 0.06:
        framing = "fit_blur"
        reason = "source already matches output shape"
        confidence = 0.95
    elif face >= 0.30 and edge < 0.55:
        framing = "track"
        reason = f"face in {face:.0%} of samples - follow subject"
        confidence = 0.65 + face * 0.3
    elif motion >= 0.30 and spread >= 0.18:
        framing = "fit_blur"
        reason = "wide action - keep the whole frame"
        confidence = 0.6 + motion * 0.3
    elif face >= 0.12:
        framing = "track"
        reason = f"subject detected in {face:.0%} of samples"
        confidence = 0.55 + face * 0.3
    elif motion >= 0.15:
        framing = "track"
        reason = "motion detected - crop follows the action"
        confidence = 0.55 + motion * 0.25
    else:
        framing = "fit_blur"
        reason = "no reliable subject - show full frame"
        confidence = 0.5

    return StyleDecision(framing=framing, focus_x=focus_x, focus_y=focus_y,
                         reason=reason, confidence=round(min(confidence, 0.97), 2),
                         source="local")


def _ai_decide(moments: list, metrics: list[dict], cfg, log: Logger) -> list[StyleDecision] | None:
    """Ask the configured provider to pick styles. Returns None on any failure."""
    from . import ai

    rows = []
    for i, (moment, m) in enumerate(zip(moments, metrics)):
        rows.append({
            "index": i,
            "title": getattr(moment, "title", "")[:80],
            "hook": getattr(moment, "hook", "")[:40],
            "duration": round(float(moment.end - moment.start), 1),
            "face_coverage": m["face_coverage"],
            "motion_coverage": m["motion_coverage"],
            "subject_x": m["cx"],
            "subject_y": m["cy"],
            "subject_spread_x": m["spread_x"],
            "source_ratio": m["source_ratio"],
            "target_ratio": m["target_ratio"],
        })
    prompt = f"""You are an expert vertical short-form video editor. For EACH moment below,
choose the framing that keeps the main subject clearly visible with correct cropping.

Choices:
- "track": crop to the target shape and follow the detected face/action. Best when one person or object is the subject.
- "fit_blur": show the entire original frame centred on a blurred background. Best for gameplay or wide action where cutting sides loses context.
- "fit_black": show the entire original frame on black bars. Best when the source already has bars or a fixed layout.

If face_coverage is high and the subject is fairly still, prefer "track".
If motion_coverage is high and subject_spread_x is large, prefer "fit_blur" so nothing important is cut.
If the source and target ratios are nearly equal, prefer "fit_blur".
focus_x/focus_y are where the subject should sit inside the crop (0.0-1.0).
Use focus_y around 0.38-0.45 so captions at the bottom do not cover the face.

Return ONLY a JSON array, no markdown, in this exact shape:
[{{"index":0,"framing":"track","focus_x":0.5,"focus_y":0.42,"reason":"short phrase","confidence":0.85}}]

MOMENTS:
{json.dumps(rows, ensure_ascii=False)}
"""
    try:
        raw = ai.ask_json(prompt, cfg)
        data = raw if isinstance(raw, list) else (raw or {}).get("styles") or (raw or {}).get("clips")
        if not isinstance(data, list) or not data:
            return None
        by_index = {}
        for item in data:
            try:
                by_index[int(item.get("index"))] = item
            except (TypeError, ValueError, AttributeError):
                continue
        if not by_index:
            return None

        decisions: list[StyleDecision] = []
        for i, (moment, m) in enumerate(zip(moments, metrics)):
            item = by_index.get(i) or {}
            framing = str(item.get("framing", "")).strip().lower()
            if framing not in ("track", "fit_blur", "fit_black"):
                local = local_decide(m, cfg)
                decisions.append(local)
                continue
            fx, fy = _focus_from(m, cfg)
            try:
                fx = clamp(float(item.get("focus_x", fx)), 0.25, 0.75)
                fy = clamp(float(item.get("focus_y", fy)), 0.28, 0.58)
            except (TypeError, ValueError):
                pass
            try:
                conf = clamp(float(item.get("confidence", 0.8)), 0, 1)
            except (TypeError, ValueError):
                conf = 0.8
            decisions.append(StyleDecision(
                framing=framing, focus_x=round(fx, 3), focus_y=round(fy, 3),
                reason=str(item.get("reason") or "AI style pick")[:100],
                confidence=round(conf, 2), source="ai",
            ))
        log.ok("AI chose styles: " + ", ".join(d.framing for d in decisions))
        return decisions
    except Exception as exc:
        log.warn("AI style selection failed (%s) - using local auto styling", exc)
        return None


def choose_styles(moments: list, track: dict | None, cfg, log: Logger,
                  info=None, ignore_rect: dict | None = None) -> list[StyleDecision]:
    """Return one framing decision per moment, in the same order."""
    configured = str(cfg.get("video.framing", "auto") or "auto").strip().lower()
    if configured not in FRAMINGS:
        configured = "auto"
    metrics = [moment_metrics(track, m, cfg, info, ignore_rect) for m in moments]

    if configured != "auto":
        focus_y = clamp(float(cfg.get("video.subject_focus_y", 0.42) or 0.42), 0.3, 0.58)
        log.info("Clip style: %s (manual)", STYLE_LABELS.get(configured, configured))
        return [StyleDecision(framing=configured, focus_x=0.5, focus_y=focus_y,
                              reason="Manual style setting", confidence=1.0,
                              source="manual") for _ in moments]

    log.info("Clip style: auto - measuring subject for each moment")
    decisions = None
    if bool(cfg.get("ai.auto_style", True)):
        provider = str(cfg.get("ai.provider", "local") or "local").lower()
        if provider in ("gemini", "claude", "openai"):
            log.info("Asking %s to choose the best style per clip", provider)
            decisions = _ai_decide(moments, metrics, cfg, log)
    if decisions is None or len(decisions) != len(moments):
        decisions = [local_decide(m, cfg) for m in metrics]
        log.info("Local auto styles: " + ", ".join(
            f"{STYLE_LABELS.get(d.framing, d.framing)} ({d.reason})" for d in decisions
        ))
    return decisions
