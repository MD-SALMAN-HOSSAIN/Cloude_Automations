from __future__ import annotations

import hashlib
import os
import re
import time
from pathlib import Path

from . import cleanup
from .util import Logger

_URL_RE = re.compile(r"^https?://", re.I)
_VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".avi"}
_BROWSERS = ("firefox", "chrome", "edge", "brave", "opera", "vivaldi")
_ALT_CLIENTS = ("tv", "android", "ios", "mweb", "web_safari")
_BOT_MARKERS = (
    "sign in to confirm",
    "confirm you're not a bot",
    "confirm you’re not a bot",
    "not a bot",
    "captcha",
    "login_required",
    "please sign in",
)


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


def _is_bot_check(error: Exception) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in _BOT_MARKERS)


def _short(error: Exception) -> str:
    return " ".join(str(error).split())[:240]


def _cookie_file(value) -> Path | None:
    """Expand a user-supplied cookies.txt path; ignore empty/missing entries."""
    raw = str(value or "").strip().strip('"')
    if not raw:
        return None
    raw = os.path.expandvars(os.path.expanduser(raw))
    path = Path(raw).expanduser()
    return path if path.is_file() else None


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
    # A previous interrupted run may have left gigabytes of yt-dlp fragments.
    # Remove them before doing anything else so retries start clean.
    cleanup.purge_download_temp(cfg, log, key=key)

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
    base_opts = {
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

    base_opts["progress_hooks"] = [hook]

    browser = str(cfg.get("download.cookies_from_browser", "auto") or "auto").strip().lower()
    if browser not in ("", "none", "auto") and browser not in _BROWSERS:
        log.warn("Unknown browser cookie setting '%s' - using Auto", browser)
        browser = "auto"
    raw_cookie_file = str(cfg.get("download.cookies_file", "") or "").strip().strip('"')
    cookie_file = _cookie_file(raw_cookie_file)
    if raw_cookie_file and cookie_file is None:
        log.warn("cookies.txt not found (%s) - ignoring it", raw_cookie_file)
    player = str(cfg.get("download.player_client", "default") or "default").strip().lower()

    def opts_for(*, cookies_browser=None, cookies_file=None, client=None):
        opts = dict(base_opts)
        opts["progress_hooks"] = [hook]
        if cookies_file:
            opts["cookiefile"] = str(cookies_file)
        if cookies_browser:
            opts["cookiesfrombrowser"] = (cookies_browser,)
        if client and client != "default":
            opts["extractor_args"] = {"youtube": {"player_client": [client]}}
        return opts

    # Explicit user choices go first. "auto" keeps the old anonymous download
    # first, then tries browsers that may have a signed-in YouTube session.
    attempts = []
    if cookie_file:
        attempts.append(("cookies.txt", opts_for(cookies_file=cookie_file, client=player), True))
    if browser not in ("", "none", "auto"):
        attempts.append((f"{browser} cookies", opts_for(cookies_browser=browser, client=player), True))
    if not attempts or browser == "auto":
        attempts.append(("no cookies", opts_for(client=player), True))
    if browser == "auto":
        for name in _BROWSERS:
            attempts.append((f"{name} cookies", opts_for(cookies_browser=name, client=player), False))

    def run_attempt(opts) -> Exception | None:
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                failed = ydl.download([source])
            if failed:
                raise RuntimeError(f"yt-dlp could not download the video ({failed})")
            result = _cached_file(out_dir, key)
            if not result:
                raise RuntimeError("Download finished but no video file was produced.")
        except Exception as exc:  # noqa - reported to the user with context below
            # Delete half-downloaded fragments immediately, before any retry.
            cleanup.purge_download_temp(cfg, log, key=key)
            return exc
        return None

    log.info("Downloading video at up to %dp (free, cached for next time)", height)
    last_error: Exception | None = None
    bot_seen = False
    readable_cookie_opts = []

    for index, (label, opts, fatal) in enumerate(attempts):
        if index:
            log.warn("YouTube blocked the download - retrying with %s", label)
        error = run_attempt(opts)
        if error is None:
            result = _cached_file(out_dir, key)
            cleanup.purge_download_temp(cfg, log, key=key)  # no stray fragments after success
            log.ok("Downloaded: %s (%.1f MB)", result.name, result.stat().st_size / 1e6)
            return result.resolve()

        last_error = error
        if _is_bot_check(error):
            bot_seen = True
            # The cookies were read successfully, but this client/IP was still
            # challenged. Those cookies are the best base for a client retry.
            if label != "no cookies":
                readable_cookie_opts.append((label, opts))
            continue
        if fatal:
            raise error
        log.warn("Could not use %s: %s", label, _short(error))

    # A bot check can also depend on the YouTube player client. Only worth
    # trying after the cookie sources above failed, to keep normal runs fast.
    if bot_seen:
        bases = readable_cookie_opts[:2] or [("no cookies", opts_for())]
        tried = set()
        for client in _ALT_CLIENTS:
            if client == player:
                continue
            for label, base in bases:
                if (client, label) in tried:
                    continue
                tried.add((client, label))
                opts = dict(base)
                opts["extractor_args"] = {"youtube": {"player_client": [client]}}
                log.warn("Trying YouTube client '%s' with %s", client, label)
                error = run_attempt(opts)
                if error is None:
                    result = _cached_file(out_dir, key)
                    cleanup.purge_download_temp(cfg, log, key=key)
                    log.ok("Downloaded: %s (%.1f MB)", result.name, result.stat().st_size / 1e6)
                    return result.resolve()
                last_error = error
                if not _is_bot_check(error):
                    log.warn("Client '%s' failed: %s", client, _short(error))

    raise RuntimeError(
        "YouTube blocked this download with a bot check ('Sign in to confirm you're not a bot'). "
        "In the AI & Output tab open 'YouTube Download' and choose the browser where you are "
        "signed in to YouTube (Firefox works best on Windows), or point it at a cookies.txt file. "
        f"Cookies stay on this PC. Last error: {_short(last_error)}"
    ) from last_error
