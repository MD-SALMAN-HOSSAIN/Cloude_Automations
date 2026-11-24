"""Shared geometry and defaults for the vertical split-screen gaming layout.

The three consumers (render graph, caption builder and pipeline) all need the
exact same boxes, so the maths lives here once instead of being copied.
"""
from __future__ import annotations

from .util import clamp

# Paste-ready instruction text shown in the AI tab and sent to optional APIs.
DEFAULT_AI_PROMPT = """Find high-energy, funny, or unexpected gaming reaction moments (10 to 25 seconds long) with a strong immediate hook in the first 2 seconds. Format in a 9:16 vertical split-screen gaming layout: place the cropped facecam on the top third and the main gameplay footage on the bottom two-thirds. Add bold, uppercase, white-with-black-outline captions positioned right at the split line or lower-middle of the gameplay screen, highlighting key action words in yellow. Keep pacing fast with zero dead air."""


def _even(value: float) -> int:
    number = max(2, int(round(float(value))))
    return number - number % 2


def split_boxes(cfg, width: int, height: int) -> dict:
    """Pixel boxes for facecam / gameplay and the blurred safety bars.

    Defaults: facecam 33% of canvas height, gameplay 57%, a 1.5% seam, plus
    small blurred bars at the very top and bottom for YouTube Shorts UI.
    """
    width, height = int(width), int(height)
    fc = clamp(float(cfg.get("video.split_facecam_ratio", 0.33) or 0.33), 0.20, 0.45)
    gp = clamp(float(cfg.get("video.split_gameplay_ratio", 0.57) or 0.57), 0.40, 0.70)
    gap = clamp(float(cfg.get("video.split_gap_ratio", 0.015) or 0.015), 0.0, 0.05)

    # Keep a little breathing room even if somebody enters extreme values.
    if fc + gp + gap > 0.97:
        gp = max(0.40, 0.97 - fc - gap)
    remaining = max(0.0, 1.0 - fc - gp - gap)
    top_ratio = remaining * 0.35  # slightly larger top bar protects the facecam

    top = _even(height * top_ratio)
    fc_h = _even(height * fc)
    gap_px = max(2, _even(height * gap))
    gp_y = top + fc_h + gap_px
    gp_h = _even(height * gp)
    if gp_y + gp_h > height:
        gp_h = _even(height - gp_y)
    bottom = max(0, height - gp_y - gp_h)

    return {
        "facecam": {"x": 0, "y": top, "w": width, "h": fc_h},
        "gameplay": {"x": 0, "y": gp_y, "w": width, "h": gp_h},
        "top_bar": top,
        "gap": gap_px,
        "bottom_bar": bottom,
    }
