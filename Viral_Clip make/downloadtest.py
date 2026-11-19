"""Offline test for download caching and live progress reporting."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import downloader
from core.util import Logger

DOWNLOADS = []


class FakeYoutubeDL:
    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def download(self, urls):
        DOWNLOADS.append(urls[0])
        hook = self.opts["progress_hooks"][0]
        hook({"status": "downloading", "downloaded_bytes": 25, "total_bytes": 100,
              "speed": 2_000_000, "eta": 4})
        hook({"status": "downloading", "downloaded_bytes": 100, "total_bytes": 100,
              "speed": 3_000_000, "eta": 0})
        hook({"status": "finished"})
        out = Path(self.opts["outtmpl"].replace("%(ext)s", "mp4"))
        out.write_bytes(b"0" * 2048)


sys.modules["yt_dlp"] = type("yt_dlp", (), {"YoutubeDL": FakeYoutubeDL})()


class Cfg:
    def __init__(self, height):
        self.downloads_dir = Path(tempfile.mkdtemp(prefix="vcm_dl_"))
        self.height = height

    def get(self, key, default=None):
        return self.height if key == "general.download_height" else default


def main():
    progress = []
    lines = []
    log = Logger(lambda level, msg: lines.append(f"[{level}] {msg}"))
    cfg = Cfg(720)

    first = downloader.fetch("https://example.com/video", cfg, log,
                             progress=lambda pct, msg: progress.append((pct, msg)))
    assert first.exists() and DOWNLOADS == ["https://example.com/video"], DOWNLOADS
    assert any(pct >= 10 for pct, _ in progress), progress
    assert any("Downloading" in msg for _, msg in progress), progress
    print("FIRST:", first.name, "| progress events:", len(progress))

    progress.clear()
    second = downloader.fetch("https://example.com/video", cfg, log,
                              progress=lambda pct, msg: progress.append((pct, msg)))
    assert second == first and len(DOWNLOADS) == 1, DOWNLOADS
    assert any("already on disk" in line for line in lines), lines
    assert any(pct == 10 for pct, _ in progress), progress
    print("CACHE: reused, no second download")

    cfg.height = 1080
    third = downloader.fetch("https://example.com/video", cfg, log)
    assert third != first and len(DOWNLOADS) == 2, DOWNLOADS
    print("QUALITY CHANGE: new cache entry", third.name)

    print("DOWNLOADER TEST PASSED")


if __name__ == "__main__":
    main()
