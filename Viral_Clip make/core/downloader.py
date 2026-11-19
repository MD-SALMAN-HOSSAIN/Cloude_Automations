from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

from .util import Logger

_URL_RE = re.compile(r"^https?://", re.I)
_VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".avi"}


def is_url(source: str) -> bool:
    return bool(_URL_RE.match(source.strip()))


def _cache_key(source: str, height: int) -> str:
    """One cache entry per URL + chosen quality, so settings changes re-download."""
    raw = f"{source.strip()}|{height}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def _cached_file(out_dir: Path, key: str) -> Path | None:
    files = [p for p in out_dir.glob(f"source_{key}.*")
             if p.suffix.lower() in _VIDEO_EXT and p.stat().st_size > 0]
    return max(files, key=lambda p: p.stat().st_size) if files else None


def fetch(source: str, cfg, log: Logger, progress=None) -> Path:
    """Return a local video path - cached download, fresh download, or local file."""
    source = source.strip().strip('"')
    if not is_url(source):
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"Local file not found: {source}")
        return path.resolve()

    height = int(cfg.get("general.download_height", 720) or 720)
    out_dir = cfg.downloads_dir
    key = _cache_key(source, height)

    cached = _cached_file(out_dir, key)
    if cached:
        log.ok("Using downloaded source already on disk (%.1f MB) - no download needed",
               cached.stat().st_size / 1e6)
        if progress:
            progress(10, "Using cached video...")
        return cached.resolve()

    import yt_dlp

    # Progressive single-file formats first: no second audio download and no
    # ffmpeg merge, which is usually the biggest time saver for 720p and below.
    fmt = (
        f"best[height<={height}][ext=mp4]/best[height<={height}]/"
        f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
        f"bestvideo[height<={height}]+bestaudio/best"
    )
    opts = {
        "format": fmt,
        "merge_output_format": "mp4",
        "outtmpl": str(out_dir / f"source_{key}.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 4,
        "socket_timeout": 60,
    }

    last_report = 0.0

    def hook(d):
        nonlocal last_report
        status = d.get("status")
        if status == "error":
            log.warn("Download warning: %s", d.get("error"))
            return
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            if not total:
                return
            pct = min(1.0, done / total)
            now = time.monotonic()
            if now - last_report < 1.5 and pct < 1.0:
                return
            last_report = now
            speed = d.get("speed") or 0
            eta = d.get("eta")
            message = (f"Downloading {pct * 100:.0f}% "
                       f"({done / 1e6:.1f}/{total / 1e6:.1f} MB")
            if speed:
                message += f", {speed / 1e6:.1f} MB/s"
            if eta:
                message += f", about {eta}s left"
            log.info("%s)", message)
            if progress:
                progress(2 + pct * 8, f"Downloading {pct * 100:.0f}%")
        elif status == "finished":
            log.info("Download complete - preparing video file")
            if progress:
                progress(10, "Preparing video...")

    opts["progress_hooks"] = [hook]
    log.info("Downloading video at up to %dp (free, cached for next time)", height)
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([source])

    result = _cached_file(out_dir, key)
    if not result:
        raise RuntimeError("Download finished but no video file was produced.")
    log.ok("Downloaded: %s (%.1f MB)", result.name, result.stat().st_size / 1e6)
    return result.resolve()
