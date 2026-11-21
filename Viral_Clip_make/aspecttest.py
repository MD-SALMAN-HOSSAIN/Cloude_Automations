"""Test caption + render pipeline at several output aspect ratios."""
from __future__ import annotations

import sys
import tempfile
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.captions import build_ass, write_ass
from core.config import Config
from core.ffmpeg_tools import RenderRequest, make_test_video, probe, render_clip
from core.util import Logger

Word = namedtuple("Word", "start end text")


def main():
    cfg = Config()
    log = Logger(lambda level, msg: print(f"[{level}] {msg}"))
    work = Path(tempfile.mkdtemp(prefix="vcm_aspect_"))
    src = make_test_video(work / "source.mp4", seconds=18)
    info = probe(src)
    words = [Word(i * 0.6, i * 0.6 + 0.45, w) for i, w in enumerate(
        "this is absolutely insane you will never believe the secret truth about it".split()
    )]
    for aspect, expected in [("9:16", (1080, 1920)), ("1:1", (1080, 1080)),
                             ("16:9", (1920, 1080)), ("custom", (900, 1600))]:
        if aspect == "custom":
            cfg.set("video.aspect", "custom")
            cfg.set("video.custom_width", 900)
            cfg.set("video.custom_height", 1600)
        else:
            cfg.set("video.aspect", aspect)
        from core.pipeline import _apply_aspect
        w, h = _apply_aspect(cfg, log)
        assert (w, h) == expected, f"aspect mapping wrong: {(w, h)} != {expected}"
        ass = work / f"cap_{aspect.replace(':', '')}.ass"
        write_ass(ass, build_ass(words, "THIS IS INSANE", 10.0, cfg, show_hook=True))
        head = ass.read_text(encoding="utf-8").splitlines()
        assert f"PlayResX: {w}" in head and f"PlayResY: {h}" in head, "ASS resolution wrong"
        out = work / f"clip_{aspect}.mp4"
        render_clip(RenderRequest(source=src, start=1, end=11, out_path=out,
                                  captions_ass=ass, source_info=info, cfg=cfg), log=log)
        result = probe(out)
        assert (result.width, result.height) == expected, f"render {aspect} wrong size"
        print(f"ASPECT {aspect}: {result.width}x{result.height} OK")
    print("ASPECT TEST PASSED")


if __name__ == "__main__":
    main()
