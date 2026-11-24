from __future__ import annotations

import re
from pathlib import Path

from .layout import split_boxes
from .util import clamp

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Hook,{font},{hook_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H78000000,-1,0,0,0,100,100,1.2,0,1,9,4,8,60,60,{hook_margin},1
Style: Cap,{font},{caption_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0.6,0,1,7,3,2,70,70,{cap_margin},1
Style: SeamCap,{font},{caption_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0.6,0,1,7,3,8,70,70,{seam_margin},1
Style: Brand,{font},{brand_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,-1,0,0,0,100,100,0.8,0,1,4,2,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _clean(text: str) -> str:
    text = (text or "").replace("{", "(").replace("}", ")").replace("\\", "/")
    return re.sub(r"\s+", " ", text).strip()


def _wrap(text: str, max_chars: int = 18, max_lines: int = 2) -> str:
    words, lines, current = text.split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
            if len(lines) == max_lines:
                break
    if current and len(lines) < max_lines:
        lines.append(current)
    return "\\N".join(lines[:max_lines])


def _groups(words, max_words=3, max_chars=17, max_gap=0.34):
    groups, current = [], []
    for word in words:
        if not word.text.strip():
            continue
        if current:
            gap = word.start - current[-1].end
            chars = len(" ".join(w.text for w in current)) + len(word.text) + 1
            if len(current) >= max_words or gap > max_gap or chars > max_chars:
                groups.append(current)
                current = []
        current.append(word)
    if current:
        groups.append(current)
    return groups


def build_ass(words, hook: str, duration: float, cfg, show_hook: bool = True,
              hook_margin: int | None = None) -> str:
    width = int(cfg.get("video.width", 1080))
    height = int(cfg.get("video.height", 1920))
    scale = width / 1080.0

    cap_size = int(int(cfg.get("style.caption_size", 88)) * scale)
    hook_size = int(int(cfg.get("style.hook_size", 104)) * scale)
    safe_bottom = int(int(cfg.get("style.safe_bottom_px", 430)) * scale)
    font = str(cfg.get("style.caption_font", "Impact"))
    highlight = str(cfg.get("style.caption_highlight", "&H0000E5FF"))
    cap_margin = max(int(60 * scale), min(int(760 * scale), safe_bottom + int(cap_size * 0.55)))
    if hook_margin is None:
        hook_margin = max(int(90 * scale), int(height * 0.13))
    hook_margin = int(hook_margin)

    split = bool(cfg.get("features.split_layout", False))
    position = str(cfg.get("style.caption_position", "seam") or "seam").strip().lower()
    seam_margin = max(int(40 * scale), int(height * 0.30))
    brand_size = max(int(22 * scale), int(height * 0.014))
    cap_style = "Cap"
    boxes = None
    if split:
        boxes = split_boxes(cfg, width, height)
        cap_style = "SeamCap" if position == "seam" else "Cap"
        seam_margin = max(int(40 * scale),
                          boxes["gameplay"]["y"] + int(height * 0.045))
        if position != "seam":
            # Lower-third of the gameplay window, clear of the Shorts UI bar.
            cap_margin = max(int(60 * scale), boxes["bottom_bar"] + int(height * 0.10))
        # Hook lives in the facecam panel; captions own the gameplay panel.
        hook_margin = boxes["top_bar"] + max(
            int(6 * scale), int(boxes["facecam"]["h"] * 0.05))
        hook_size = min(hook_size, max(
            int(46 * scale), int(boxes["facecam"]["h"] * 0.26)))

    header = ASS_HEADER.format(width=width, height=height, font=font,
                               hook_size=hook_size, caption_size=cap_size,
                               hook_margin=hook_margin, cap_margin=cap_margin,
                               seam_margin=seam_margin, brand_size=brand_size)
    events = []
    watermark = str(cfg.get("style.watermark", "") or "").strip()
    if split and watermark:
        bx = boxes["gameplay"]["x"] + int(28 * scale)
        by = boxes["gameplay"]["y"] + int(18 * scale)
        events.append(
            f"Dialogue: 2,0:00:00.00,{_ts(duration)},Brand,,0,0,0,,"
            f"{{\\an7\\pos({bx},{by})}}{_clean(watermark)}"
        )
    if show_hook:
        hook_text = _clean(hook).upper()
        if hook_text:
            keep = clamp(float(cfg.get("style.hook_duration", 2.8)), 0.8, min(6.0, duration))
            events.append(
                f"Dialogue: 1,{_ts(0)},{_ts(keep)},Hook,,0,0,0,,"
                f"{{\\fad(70,220)\\t(0,110,\\fscx112\\fscy112)\\t(110,260,\\fscx100\\fscy100)}}"
                f"{_wrap(hook_text, max(12, int(18 * scale)))}"
            )

    for group in _groups(words, max_chars=max(12, int(17 * scale))):
        group_end = clamp(group[-1].end + 0.10, 0, duration)
        for idx, word in enumerate(group):
            start = clamp(word.start, 0, duration)
            end = clamp(group[idx + 1].start if idx + 1 < len(group) else max(group_end, start + 0.12),
                        start + 0.05, duration)
            if end <= start:
                continue
            parts = []
            for j, other in enumerate(group):
                text = _clean(other.text).upper()
                if j == idx:
                    parts.append(f"{{\\c{highlight}\\fscx106\\fscy106}}{text}{{\\c&H00FFFFFF&\\fscx100\\fscy100}}")
                else:
                    parts.append(text)
            events.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},{cap_style},,0,0,0,,{' '.join(parts)}")
    return header + "\n".join(events) + "\n"


def write_ass(path: Path, text: str):
    path.write_text(text, encoding="utf-8")
