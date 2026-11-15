from __future__ import annotations

import re
from pathlib import Path

from .ffmpeg import ffmpeg_bin
from .util import LOG

_URL_RE = re.compile(r"^https?://", re.I)


class DownloadError(RuntimeError):
    pass


def _base_opts(cfg) -> dict:
    downloads = cfg.data_dir / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    max_height = int(cfg.get("download.max_height", 1080))
    opts = {
        "outtmpl": str(downloads / "%(id)s.%(ext)s"),
        "format": (
            f"bv*[height<={max_height}][ext=mp4]+ba[ext=m4a]/"
            f"b[height<={max_height}][ext=mp4]/bv*[height<={max_height}]+ba/b"
        ),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "fragment_retries": 5,
        "ffmpeg_location": str(Path(ffmpeg_bin()).parent),
        "restrictfilenames": True,
        "windowsfilenames": True,
        "overwrites": False,
    }
    browser = (cfg.get("download.cookies_from_browser") or "").strip()
    if browser:
        opts["cookiesfrombrowser"] = (browser,)
    cookies_file = (cfg.get("download.cookies_file") or "").strip()
    if cookies_file:
        opts["cookiefile"] = cookies_file
    return opts


def inspect(url: str, cfg) -> dict:
    """Metadata only - title, duration, id. Also expands playlists (capped)."""
    import yt_dlp

    if not _URL_RE.match(url or ""):
        raise DownloadError(f"that does not look like a URL: {url!r}")
    opts = _base_opts(cfg)
    # Playlist links must expand here, otherwise a whole playlist is invisible.
    opts["noplaylist"] = False
    opts["extract_flat"] = "in_playlist"
    opts["playlistend"] = int(cfg.get("download.max_playlist_items", 20) or 20)
    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except Exception as exc:  # noqa: BLE001
            raise DownloadError(_friendly_error(exc)) from exc

    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise DownloadError("playlist is empty or private")
        out = []
        for entry in entries:
            if entry.get("_type") == "url":
                continue
            out.append(
                {
                    "url": entry.get("webpage_url") or entry.get("url") or f"https://www.youtube.com/watch?v={entry.get('id')}",
                    "video_id": entry.get("id"),
                    "title": entry.get("title") or "Untitled",
                    "duration": float(entry.get("duration") or 0),
                    "uploader": entry.get("uploader") or "",
                }
            )
        LOG.info("playlist detected: %d video(s)", len(out))
        return {"playlist": out}

    return {
        "url": info.get("webpage_url") or url,
        "video_id": info.get("id"),
        "title": info.get("title") or "Untitled",
        "duration": float(info.get("duration") or 0),
        "uploader": info.get("uploader") or "",
        "is_live": bool(info.get("is_live")),
    }


def download(url: str, cfg, video_id: str | None = None) -> dict:
    import yt_dlp

    max_minutes = float(cfg.get("download.max_minutes", 180))
    opts = _base_opts(cfg)
    if video_id:
        opts["outtmpl"] = str(cfg.data_dir / "downloads" / f"{video_id}.%(ext)s")

    # Reuse an existing download.
    if video_id:
        for existing in (cfg.data_dir / "downloads").glob(f"{video_id}.*"):
            if existing.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"} and existing.stat().st_size > 100_000:
                LOG.info("using existing download %s", existing.name)
                return {"path": existing, "video_id": video_id, "title": existing.stem, "duration": 0}

    LOG.info("downloading %s", url)
    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            info = ydl.extract_info(url, download=True)
        except Exception as exc:  # noqa: BLE001
            raise DownloadError(_friendly_error(exc)) from exc
        if info.get("_type") == "playlist":
            entries = [e for e in (info.get("entries") or []) if e]
            if not entries:
                raise DownloadError("playlist is empty")
            info = entries[0]
        path = Path(ydl.prepare_filename(info))
        if not path.exists():
            candidates = sorted(
                (cfg.data_dir / "downloads").glob(f"{info.get('id')}.*"),
                key=lambda p: p.stat().st_size,
                reverse=True,
            )
            if not candidates:
                raise DownloadError("download finished but the file is missing")
            path = candidates[0]

    duration = float(info.get("duration") or 0)
    if max_minutes and duration and duration > max_minutes * 60:
        LOG.warning("video is %.0f min long; processing the first %.0f min only", duration / 60, max_minutes)
    LOG.info("downloaded %.1f MB -> %s", path.stat().st_size / 1_000_000, path.name)
    return {
        "path": path,
        "video_id": info.get("id"),
        "title": info.get("title") or path.stem,
        "duration": duration,
        "uploader": info.get("uploader") or "",
    }


def _friendly_error(exc: Exception) -> str:
    text = str(exc)
    low = text.lower()
    if "age" in low and ("confirm" in low or "restricted" in low):
        return (
            "YouTube is asking for age verification. Add cookies (download.cookies_from_browser = \"chrome\" "
            "in config.toml), or download your own copy and queue it with: shorts file <path>. Original error: " + text[:300]
        )
    if "sign in" in low or "private" in low or "members" in low:
        return "Video is private, members-only or requires sign-in. Use a public video you own. Original: " + text[:300]
    if "unsupported url" in low:
        return "Unsupported URL. Pass a YouTube video link (or a playlist link). Original: " + text[:200]
    if "403" in low or "forbidden" in low:
        return "YouTube refused the download (403). Update yt-dlp: python -m pip install -U yt-dlp. Original: " + text[:300]
    return text[:500]
