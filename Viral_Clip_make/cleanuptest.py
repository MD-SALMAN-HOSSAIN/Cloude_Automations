"""Verify failed-run cleanup removes partial data but keeps good files."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.cleanup import (is_temp_file, purge_all, purge_download_temp,
                          remove_clip_artifacts)
from core.util import Logger


def main():
    work = Path(tempfile.mkdtemp(prefix="vcm_cleanuptest_"))
    downloads = work / "downloads"
    cache = work / "cache"
    workdir = work / "work"
    out = work / "output"
    for folder in (downloads, cache, workdir, out):
        folder.mkdir()

    class FakeConfig:
        downloads_dir = downloads
        cache_dir = cache
        work_dir = workdir

        def get(self, key, default=None):
            return str(out) if key == "general.output_dir" else default

    cfg = FakeConfig()
    log = Logger(lambda level, msg: print(f"[{level}] {msg}"))

    # Partial download artefacts from a botched run.
    partials = [
        downloads / "source_abc.temp.mp4",
        downloads / "source_abc.f140.m4a",
        downloads / "source_abc.f299.mp4",
        downloads / "source_abc.mp4.part",
        downloads / "source_abc.mp4.part-Frag12.part",
        downloads / "source_abc.mp4.ytdl",
        downloads / "source_empty.mp4",  # zero-byte download
    ]
    for path in partials:
        path.write_bytes(b"" if path.name == "source_empty.mp4" else b"x" * 128)
    assert all(is_temp_file(p) or p.stat().st_size == 0 for p in partials)

    # Things that must survive.
    good_video = downloads / "source_good.mp4"
    good_video.write_bytes(b"v" * 512)
    good_json = downloads / "source_good.mp4.transcript.json"
    good_json.write_text('{"words": [1]}', encoding="utf-8")
    good_track = cache / "source_good.track.json"
    good_track.write_text('{"samples": [1]}', encoding="utf-8")

    # Dead caches.
    (downloads / "source_gone.mp4.transcript.json").write_text('{"words": []}', encoding="utf-8")
    (cache / "source_gone.track.json").write_text('{"samples": []}', encoding="utf-8")
    (cache / "broken.track.json").write_text("not json", encoding="utf-8")

    # Unfinished render leftovers.
    day = out / "2026-11-22"
    day.mkdir()
    half = day / "clip01.mp4"
    half.write_bytes(b"x" * 64)
    ass = day / "clip01.ass"
    ass.write_text("[Script Info]", encoding="utf-8")
    (day / "filter_graph.txt").write_text("graph", encoding="utf-8")
    (day / "clip01.jpg").write_bytes(b"img")
    finished = day / "clip00.mp4"
    finished.write_bytes(b"done" * 64)

    freed = purge_all(cfg, log)
    assert freed > 0

    for path in partials:
        assert not path.exists(), f"partial was kept: {path.name}"
    for path in (good_video, good_json, good_track, finished):
        assert path.exists(), f"good file was deleted: {path.name}"
    for path in (day / "clip01.mp4", ass, day / "filter_graph.txt", day / "clip01.jpg"):
        assert not path.exists(), f"render leftover was kept: {path.name}"
    assert not (downloads / "source_gone.mp4.transcript.json").exists()
    assert not (cache / "source_gone.track.json").exists()

    # A second pass must be harmless (idempotent).
    assert purge_all(cfg) == 0

    # Per-clip cleanup removes only that clip's artifacts.
    p2 = day / "clip02.mp4"
    p2.write_bytes(b"half")
    a2 = day / "clip02.ass"
    a2.write_text("x", encoding="utf-8")
    remove_clip_artifacts(p2, a2, log)
    assert not p2.exists() and not a2.exists() and finished.exists()

    # Key-scoped download cleanup leaves other sources alone.
    (downloads / "source_other.part").write_bytes(b"x")
    (downloads / "source_abc.part").write_bytes(b"x")
    purge_download_temp(cfg, key="other")
    assert not (downloads / "source_other.part").exists()
    assert (downloads / "source_abc.part").exists()

    print("CLEANUP TEST PASSED")
    print("freed bytes:", freed)


if __name__ == "__main__":
    main()
