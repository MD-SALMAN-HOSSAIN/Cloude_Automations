"""Automatic cleanup of failed downloads, partial clips and dead caches.

Nothing here deletes a finished video or a healthy cache entry. Only three
kinds of files are removed:

* yt-dlp leftovers (.part, .ytdl, .temp.mp4, .f140.m4a, .part-Frag*.part)
* files belonging to a render that did not finish (.ass, filter_graph.txt,
  zero-byte mp4, and its thumbnail/metadata sidecars)
* cache files whose source video is gone or whose JSON is unreadable
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .util import Logger

VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".avi"}
_TEMP_END = (".part", ".ytdl", ".tmp")
_FRAGMENT = re.compile(r"\.f\d+\.(?:mp4|m4a|webm|mkv|mov|avi)$", re.I)
_TEMP_INFIX = re.compile(r"\.temp\.(?:mp4|mkv|webm|mov|avi)$", re.I)
_TRANSCRIPT_SUFFIX = ".transcript.json"
_TRACK_SUFFIX = ".track.json"


def is_temp_file(path: Path) -> bool:
    """True for partial/fragment files left by an interrupted download."""
    name = path.name.lower()
    return (
        name.endswith(_TEMP_END)
        or bool(_TEMP_INFIX.search(name))
        or bool(_FRAGMENT.search(name))
        or ".part-" in name
    )


def _delete(path: Path, log: Logger | None = None) -> int:
    try:
        size = path.stat().st_size
        path.unlink()
        return size
    except FileNotFoundError:
        return 0
    except Exception as exc:
        if log:
            log.warn("Could not delete %s: %s", path.name, exc)
        return 0


def _valid_json(path: Path) -> bool:
    try:
        return bool(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return False


def purge_download_temp(cfg, log: Logger | None = None, key: str | None = None) -> int:
    """Remove yt-dlp partial files. Returns bytes freed."""
    try:
        files = list(cfg.downloads_dir.iterdir())
    except Exception:
        return 0
    freed = 0
    count = 0
    for path in files:
        if not path.is_file():
            continue
        if key and f"source_{key}" not in path.name:
            continue
        if is_temp_file(path) or path.stat().st_size == 0:
            freed += _delete(path, log)
            count += 1
    if count and log:
        log.info("Deleted %d partial download file%s (%.1f MB)",
                 count, "" if count == 1 else "s", freed / 1e6)
    return freed


def purge_orphan_cache(cfg, log: Logger | None = None) -> int:
    """Remove caches whose video is missing or whose JSON is corrupt."""
    downloads = cfg.downloads_dir
    cache = cfg.cache_dir
    freed = 0
    count = 0

    for path in list(downloads.glob("*" + _TRANSCRIPT_SUFFIX)):
        source = Path(str(path)[:-len(_TRANSCRIPT_SUFFIX)])
        if not source.exists() or not _valid_json(path):
            freed += _delete(path, log)
            count += 1

    for path in list(cache.glob("*" + _TRACK_SUFFIX)):
        stem = path.name[:-len(_TRACK_SUFFIX)]
        try:
            has_video = any(
                q.suffix.lower() in VIDEO_EXT and q.stat().st_size > 0
                for q in downloads.glob(stem + ".*")
            )
        except Exception:
            has_video = True
        if not has_video or not _valid_json(path):
            freed += _delete(path, log)
            count += 1

    for folder in (cfg.work_dir, cache):
        try:
            entries = list(folder.iterdir())
        except Exception:
            continue
        for path in entries:
            if path.is_file() and (is_temp_file(path) or path.stat().st_size == 0):
                freed += _delete(path, log)
                count += 1

    if count and log:
        log.info("Deleted %d stale cache/temp file%s (%.1f MB)",
                 count, "" if count == 1 else "s", freed / 1e6)
    return freed


def purge_output_temp(cfg, log: Logger | None = None) -> int:
    """Remove unfinished clips and their sidecars from old failed runs.

    A video is considered unfinished only when it is empty or still has the
    caption file that a successful render always deletes. Finished clips that
    happen to be small are never touched.
    """
    root = Path(str(cfg.get("general.output_dir", "") or ""))
    if not root.exists():
        return 0
    try:
        entries = [p for p in root.rglob("*") if p.is_file()]
    except Exception:
        return 0

    videos = [p for p in entries if p.suffix.lower() in VIDEO_EXT]
    ass_files = {p for p in entries if p.suffix.lower() == ".ass"}
    unfinished = set()
    for video in videos:
        try:
            if video.stat().st_size == 0 or video.with_suffix(".ass") in ass_files:
                unfinished.add(video)
        except Exception:
            continue

    targets = set(unfinished) | ass_files
    targets.update(p for p in entries if p.name.lower() == "filter_graph.txt")
    for video in unfinished:
        for ext in (".jpg", ".txt", ".json", ".ass"):
            sidecar = video.with_suffix(ext)
            if sidecar.exists():
                targets.add(sidecar)

    freed = 0
    for path in targets:
        freed += _delete(path, log)
    if targets and log:
        log.info("Deleted %d leftover render file%s (%.1f MB)",
                 len(targets), "" if len(targets) == 1 else "s", freed / 1e6)
    return freed


def remove_clip_artifacts(out_path: Path, ass_path: Path | None = None,
                          log: Logger | None = None) -> int:
    """Delete every file a single unfinished clip created."""
    targets = [
        out_path,
        out_path.with_suffix(".jpg"),
        out_path.with_suffix(".txt"),
        out_path.with_suffix(".json"),
        out_path.parent / "filter_graph.txt",
        ass_path,
    ]
    freed = 0
    for path in targets:
        if path and path.exists():
            freed += _delete(path, log)
    if freed and log:
        log.info("Removed unfinished clip files (%.1f MB)", freed / 1e6)
    return freed


def purge_failed_run(cfg, log: Logger | None = None) -> int:
    """Called the moment a run fails: clean all temp, partial and dead cache data."""
    freed = purge_download_temp(cfg, log)
    freed += purge_orphan_cache(cfg, log)
    freed += purge_output_temp(cfg, log)
    return freed


def purge_all(cfg, log: Logger | None = None) -> int:
    """Startup / manual 'Clean temp files now'."""
    return purge_failed_run(cfg, log)
