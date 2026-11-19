"""Quick offline smoke test: ffmpeg render + local moment scoring + captions."""
from __future__ import annotations

import sys
import tempfile
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.captions import build_ass, write_ass
from core.config import Config
from core.ffmpeg_tools import RenderRequest, make_test_video, probe, render_clip
from core.moments import find_moments
from core.util import Logger

Word = namedtuple("Word", "start end text")


def main():
    cfg = Config()
    cfg.set("general.min_seconds", 8)
    cfg.set("general.max_seconds", 20)
    log = Logger(lambda level, msg: print(f"[{level}] {msg}"))

    work = Path(tempfile.mkdtemp(prefix="vcm_selftest_"))
    print("work:", work)

    # 1. Generate a real video and probe it ---------------------------------
    src = make_test_video(work / "source.mp4", seconds=24)
    info = probe(src)
    print(f"probe: {info.width}x{info.height}, {info.duration:.1f}s, audio={info.has_audio}")

    # 2. Render a vertical clip with burned captions ------------------------
    words = [Word(i * 0.6, i * 0.6 + 0.45, w) for i, w in enumerate(
        "this is absolutely insane you will never believe the secret truth about it".split()
    )]
    ass = work / "captions.ass"
    write_ass(ass, build_ass(words, "THIS IS INSANE", 12.0, cfg, show_hook=True))

    out = work / "clip.mp4"
    render_clip(RenderRequest(source=src, start=2, end=14, out_path=out,
                              captions_ass=ass, source_info=info, cfg=cfg,
                              framing="fit_blur"), log=log)
    result = probe(out)
    assert result.width == 1080 and result.height == 1920, "wrong output size"
    print(f"whole-frame render OK: {result.width}x{result.height}, {result.duration:.1f}s")

    # 2b. Subject-follow crop still works -----------------------------------
    out2 = work / "clip_track.mp4"
    render_clip(RenderRequest(source=src, start=2, end=8, out_path=out2,
                              source_info=info, cfg=cfg, framing="track"), log=log)
    result2 = probe(out2)
    assert result2.width == 1080 and result2.height == 1920, "wrong crop size"
    print("subject-crop render OK:", result2.width, "x", result2.height)

    from core.hardware import summary_line

    print("hardware:", summary_line())

    # 3. Local viral-moment scoring -----------------------------------------
    fake = {
        "words": [{"start": i * 0.5, "end": i * 0.5 + 0.4, "text": w}
                  for i, w in enumerate(
                      "why would anyone do this the secret truth is actually insane and you will never believe what happened next".split()
                  )],
        "segments": [{"start": 0, "end": 16, "text": "why would anyone do this"}],
        "duration": 24,
    }
    rms = [0.08 + (0.05 if i % 8 < 4 else 0) for i in range(96)]
    moments = find_moments(fake, rms, cfg, log)
    assert moments, "no moments detected"
    for m in moments:
        print(f"  moment {m.start:5.1f}-{m.end:5.1f}s  score={m.score:5.1f}  hook={m.hook!r}")

    print("\nSELFTEST PASSED")


if __name__ == "__main__":
    main()
