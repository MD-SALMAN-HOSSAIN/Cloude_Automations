from __future__ import annotations

import re
from pathlib import Path

from .util import Logger

_URL_RE = re.compile(r"^https?://", re.I)


def is_url(source: str) -> bool:
    return bool(_URL_RE.match(source.strip()))


def fetch(source: str, cfg, log: Logger) -> Path:
    """Return a local video path - either the given file or a yt-dlp download."""
    source = source.strip().strip('"')
    if not is_url(source):
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"Local file not found: {source}")
        return path.resolve()

    import yt_dlp

    out_dir = cfg.downloads_dir
    out_tmpl = str(out_dir / "source.%(ext)s")
    # Remove any stale download from a previous run.
    for old in out_dir.glob("source.*"):
        try:
            old.unlink()
        except Exception:
            pass

    log.info("Downloading video (yt-dlp, free)")
    opts = {
        "format": (
            f"bestvideo[height<={int(cfg.get('general.download_height', 1080))}][ext=mp4]"
            f"+bestaudio[ext=m4a]/best[height<={int(cfg.get('general.download_height', 1080))}]/best"
        ),
        "merge_output_format": "mp4",
        "outtmpl": out_tmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 2,
        "socket_timeout": 30,
    }

    def hook(d):
        if d.get("status") == "error":
            log.warn("Download warning: %s", d.get("error"))

    opts["progress_hooks"] = [hook]
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([source])

    files = [p for p in out_dir.glob("source.*")
             if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".avi"}]
    if not files:
        raise RuntimeError("Download finished but no video file was produced.")
    result = max(files, key=lambda p: p.stat().st_size)
    log.ok("Downloaded: %s (%.1f MB)", result.name, result.stat().st_size / 1e6)
    return result.resolve()
