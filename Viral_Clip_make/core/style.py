"""Game-character-first framing for vertical shorts.

The style engine answers one question for every moment: where is the game
character, and how do we crop so that character fills the vertical frame?

`track` is the only output framing. Whole-frame modes (`fit_blur`, `fit_black`)
make the character tiny next to the HUD, minimap and background, which is the
exact problem this lock fixes. AI and manual settings may still *suggest* a
look, but `choose_styles()` always converts the decision to a subject-follow
crop so the character is the main subject no matter what was configured.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from .faces import subject_point
from .util import Logger, clamp

FRAMINGS = ("auto", "track", "fit_blur", "fit_black")

STYLE_LABELS = {
    "auto": "AI picks best",
    "track": "Follow subject (crop)",
    "fit_blur": "Whole frame (blurred)",
    "fit_black": "Whole frame (black bars)",
    "layout": "Layout template",
}


@dataclass
class StyleDecision:
    framing: str = "auto"
    focus_x: float = 0.5      # where the subject sits horizontally in the crop
    focus_y: float = 0.42     # slightly above centre: captions use the bottom
    reason: str = ""
    confidence: float = 0.0
    source: str = "local"     # ai | local | manual
    subject: str = "game character"  # what the crop is locked onto
    locked: bool = True       # game-subject lock is always enforced

    def as_dict(self) -> dict:
        return asdict(self)


def _empty_metrics() -> dict:
    return {
        "face_coverage": 0.0, "motion_coverage": 0.0,
        "cx": 0.5, "cy": 0.5, "spread_x": 0.0, "spread_y": 0.0,
        "edge_hits": 0.0, "samples": 0,
        "source_ratio": 16 / 9, "target_ratio": 9 / 16, "subject": "center",
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
    kinds: list[str] = []
    for s in inside:
        x, y, kind, conf = subject_point(s, ignore_rect)
        if conf <= 0.05:
            continue
        kinds.append(kind)
        all_x.append(x); all_y.append(y)
        if kind == "character":
            faces.append((x, y))
        else:
            motions.append((x, y))

    points = faces + motions
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
        result["subject"] = max(set(kinds), key=kinds.count) if kinds else "center"
    return result


def _focus_from(metrics: dict, cfg) -> tuple[float, float]:
    x = clamp(float(metrics.get("cx", 0.5)), 0.32, 0.68)
    y = clamp(float(cfg.get("video.subject_focus_y", 0.42) or 0.42), 0.30, 0.58)
    # A subject already low in the frame would be hidden by captions; raise it.
    if float(metrics.get("cy", 0.5)) > 0.62:
        y = min(y, 0.38)
    return round(x, 3), round(y, 3)


def local_decide(metrics: dict, cfg) -> StyleDecision:
    """Free, deterministic pick: always crop to the game character.

    Wide action, a HUD-heavy frame or a source that already matches the target
    shape used to trigger `fit_blur`. That left the character small among the
    background and was the main framing complaint. It is no longer an option.
    """
    focus_x, focus_y = _focus_from(metrics, cfg)
    face = float(metrics.get("face_coverage", 0))
    motion = float(metrics.get("motion_coverage", 0))
    subject = str(metrics.get("subject") or "center")

    if subject == "character" and face >= 0.12:
        reason = f"game character visible in {face:.0%} of samples - lock crop onto them"
        confidence = 0.78 + face * 0.18
    elif motion >= 0.15 or subject == "motion":
        reason = "centre-of-action tracking - keep the game character dominant"
        confidence = 0.70 + motion * 0.18
    else:
        reason = "no clear subject - centre crop still keeps the character in frame"
        confidence = 0.65

    return StyleDecision(framing="track", focus_x=focus_x, focus_y=focus_y,
                         reason=reason, confidence=round(min(confidence, 0.97), 2),
                         source="local", subject=subject)


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
            "subject": m["subject"],
            "source_ratio": m["source_ratio"],
            "target_ratio": m["target_ratio"],
        })
    prompt = f"""You are a vertical short-form editor for GAMEPLAY clips. The game character
must be the main subject of every clip. Choose "track" for EVERY moment: a crop that
follows the detected character / centre of action and fills the vertical frame.

Never choose "fit_blur" or "fit_black". Showing the whole 16:9 frame makes the
character tiny next to the HUD, minimap and background, which is forbidden here -
even for wide action, effects, or sources that already match the target shape.

focus_x/focus_y say where the character should sit inside the crop (0.0-1.0).
Use focus_x 0.35-0.65 and focus_y 0.38-0.45 so the bottom captions do not cover them.

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
            if framing != "track":
                # Whole-frame suggestions are rejected here as well as in the
                # final lock, so the log explains what happened at the source.
                if framing in ("fit_blur", "fit_black"):
                    log.warn("AI suggested %s for moment %d - game-subject lock requires a crop",
                             framing, i)
                decisions.append(local_decide(m, cfg))
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
                framing="track", focus_x=round(fx, 3), focus_y=round(fy, 3),
                reason=str(item.get("reason") or "AI style pick")[:100],
                confidence=round(conf, 2), source="ai",
                subject=str(m.get("subject") or "game character"),
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
        log.info("Clip style: %s (manual)", STYLE_LABELS.get(configured, configured))
        decisions = []
        for m in metrics:
            fx, fy = _focus_from(m, cfg)
            decisions.append(StyleDecision(
                framing=configured, focus_x=fx, focus_y=fy,
                reason="Manual style setting", confidence=1.0, source="manual",
            ))
    else:
        log.info("Clip style: auto - measuring the game character for each moment")
        decisions = None
        if bool(cfg.get("ai.auto_style", True)):
            provider = str(cfg.get("ai.provider", "local") or "local").lower()
            if provider in ("gemini", "claude", "openai"):
                log.info("Asking %s where the game character is in each clip", provider)
                decisions = _ai_decide(moments, metrics, cfg, log)
        if decisions is None or len(decisions) != len(moments):
            decisions = [local_decide(m, cfg) for m in metrics]
            log.info("Local game-subject styles: " + ", ".join(d.reason for d in decisions))

    # Final guard: no matter what AI or settings asked for, every clip leaves
    # here as a character-follow crop. This is the "main subject no matter what"
    # guarantee, applied in one place so no code path can bypass it.
    overridden = 0
    for decision, m in zip(decisions, metrics):
        if decision.framing != "track":
            decision.framing = "track"
            decision.reason = "game-subject lock - character stays the main subject"
            decision.locked = True
            overridden += 1
        decision.subject = str(m.get("subject") or "game character")
    if overridden:
        log.info("Game-subject lock: upgraded %d whole-frame decision(s) to a character crop",
                 overridden)
    return decisions
