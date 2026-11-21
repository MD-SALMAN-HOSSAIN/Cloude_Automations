"""End-to-end test with the local Whisper model on a generated video."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.config import Config
from core.ffmpeg_tools import make_test_video
from core.pipeline import run_pipeline
from core.util import Logger


def main():
    cfg = Config()
    cfg.set("whisper.model", "tiny")
    cfg.set("general.min_seconds", 8)
    cfg.set("general.max_seconds", 16)
    cfg.set("general.clips_per_video", 2)
    cfg.set("general.max_minutes", 1)
    work = Path(tempfile.mkdtemp(prefix="vcm_fulltest_"))
    if len(sys.argv) > 1:
        source = Path(sys.argv[1]).resolve()
    else:
        source = make_test_video(work / "source.mp4", seconds=30)
    out = work / "output"
    cfg.set("general.output_dir", str(out))
    log = Logger(lambda level, msg: print(f"[{level}] {msg}"))
    result = run_pipeline(str(source), cfg, log,
                          lambda pct, msg: print(f"  {pct:5.1f}%  {msg}"))
    print("\nRESULT:", result)
    for clip in result["clips"]:
        assert Path(clip["path"]).exists(), "missing clip"
    print("FULLTEST PASSED")


if __name__ == "__main__":
    main()
