"""Offline test for download caching, live progress and bot-check fallback."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import downloader
from core.util import Logger

DOWNLOADS = []
CALL_OPTS = []


class FakeBotError(Exception):
    pass


class FakeYoutubeDL:
    fail_bot_once = False

    def __init__(self, opts):
        self.opts = opts
        CALL_OPTS.append(opts)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def download(self, urls):
        DOWNLOADS.append(urls[0])
        if FakeYoutubeDL.fail_bot_once and len(DOWNLOADS) == 1:
            raise FakeBotError("ERROR: Sign in to confirm you're not a bot")
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
    def __init__(self, height, **settings):
        self.downloads_dir = Path(tempfile.mkdtemp(prefix="vcm_dl_"))
        self.height = height
        self.settings = settings

    def get(self, key, default=None):
        if key == "general.download_height":
            return self.height
        return self.settings.get(key, default)


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

    # Simulate YouTube's bot challenge on the anonymous attempt. The app should
    # retry automatically with cookies from an installed browser.
    lines.clear()
    DOWNLOADS.clear()
    CALL_OPTS.clear()
    FakeYoutubeDL.fail_bot_once = True
    fourth = downloader.fetch("https://example.com/bot-check", Cfg(720), log)
    FakeYoutubeDL.fail_bot_once = False
    assert fourth.exists() and len(DOWNLOADS) == 2, DOWNLOADS
    assert any("firefox cookies" in line.lower() for line in lines), lines
    assert CALL_OPTS[1].get("cookiesfrombrowser") == ("firefox",), CALL_OPTS[1]
    print("BOT CHECK: retried with firefox cookies", fourth.name)

    print("DOWNLOADER TEST PASSED")


if __name__ == "__main__":
    main()
