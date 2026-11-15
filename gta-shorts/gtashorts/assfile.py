from __future__ import annotations

import re
from dataclasses import dataclass

from .util import clamp

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Hook,{font},{hook_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H78000000,-1,0,0,0,100,100,1.2,0,1,9,4,8,60,60,{hook_margin},1
Style: Cap,{font},{caption_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0.6,0,1,7,3,2,70,70,{cap_margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


@dataclass
class Word:
    start: float
    end: float
    text: str

    @property
    def duration(self) -> float:
        return max(0.05, self.end - self.start)


def _ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def _clean(text: str) -> str:
    text = (text or "").replace("{", "(").replace("}", ")").replace("\\", "/")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _wrap(text: str, max_chars: int = 17, max_lines: int = 2) -> str:
    words = text.split()
    lines: list[str] = []
    current = ""
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


def _groups(words: list[Word], max_words: int = 3, max_chars: int = 17, max_gap: float = 0.34) -> list[list[Word]]:
    groups: list[list[Word]] = []
    current: list[Word] = []
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


def build_ass(
    words: list[Word],
    hook: str,
    duration: float,
    cfg,
    highlight: str | None = None,
    uppercase: bool = True,
) -> str:
    """Captions with a per-word highlight + a big hook card for the first ~2.8s."""
    width = int(cfg.get("video.width", 1080))
    height = int(cfg.get("video.height", 1920))
    cap_size = int(cfg.get("video.caption_size", 86))
    hook_size = int(cfg.get("video.hook_size", 104))
    safe_bottom = int(cfg.get("video.safe_bottom_px", 430))
    font = str(cfg.get("video.caption_font", "Impact"))
    highlight = highlight or str(cfg.get("video.caption_highlight", "&H0000E5FF"))

    cap_margin = max(80, min(760, safe_bottom + int(cap_size * 0.55)))
    hook_margin = max(90, int(height * 0.13))

    header = ASS_HEADER.format(
        font=font,
        hook_size=hook_size,
        caption_size=cap_size,
        hook_margin=hook_margin,
        cap_margin=cap_margin,
    )
    assert width == 1080 and height == 1920  # the style values above assume 1080x1920

    events: list[str] = []

    hook_text = _clean(hook).upper()
    if hook_text:
        keep = clamp(float(cfg.get("video.hook_duration", 2.8)), 0.8, min(6.0, duration))
        hook_event = (
            f"Dialogue: 1,{_ts(0)},{_ts(keep)},Hook,,0,0,0,,"
            f"{{\\fad(70,220)\\t(0,110,\\fscx112\\fscy112)\\t(110,260,\\fscx100\\fscy100)}}"
            f"{_wrap(hook_text, 18)}"
        )
        events.append(hook_event)

    for group in _groups(words):
        if not group:
            continue
        group_end = clamp(group[-1].end + 0.10, 0, duration)
        for idx, word in enumerate(group):
            start = clamp(word.start, 0, duration)
            if idx + 1 < len(group):
                end = clamp(group[idx + 1].start, start + 0.05, duration)
            else:
                end = max(group_end, start + 0.12)
            if end <= start:
                continue
            parts = []
            for j, other in enumerate(group):
                text = _clean(other.text).upper() if uppercase else _clean(other.text)
                if j == idx:
                    parts.append(f"{{\\c{highlight}\\fscx106\\fscy106}}{text}{{\\c&H00FFFFFF&\\fscx100\\fscy100}}")
                else:
                    parts.append(text)
            line = f"Dialogue: 0,{_ts(start)},{_ts(end)},Cap,,0,0,0,,{' '.join(parts)}"
            events.append(line)

    return header + "\n".join(events) + "\n"


def write_ass(path, text: str):
    path.write_text(text, encoding="utf-8")


def group_words_for_display(words: list[Word]) -> list[list[Word]]:
    return _groups(words)
