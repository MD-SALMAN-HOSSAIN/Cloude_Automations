from __future__ import annotations

import json
from datetime import datetime, timedelta

from . import youtube
from .db import DB
from .notify import notify
from .util import LOG, now_iso, read_json


def _payload(clip: dict, meta: dict) -> dict:
    try:
        hashtags = json.loads(clip.get("hashtags") or "[]")
    except Exception:
        hashtags = meta.get("hashtags") or []
    title = clip.get("title") or meta.get("title") or "GTA 6 Short"
    description = clip.get("description") or meta.get("description") or ""
    if not description:
        description = " ".join(hashtags) + " #Shorts"
    return {
        "clip_id": clip["id"],
        "path": clip["path"],
        "title": title,
        "description": description,
        "hashtags": hashtags,
        "score": clip.get("score"),
    }


def post_next(cfg, db: DB, min_score: float | None = None, dry_run: bool = False) -> dict | None:
    """Upload the best ready clip."""
    min_score = float(min_score if min_score is not None else cfg.get("gemini.min_viral_score", 72))
    clip = db.next_ready_clip(min_score)
    if not clip:
        LOG.warning("no ready clip with score >= %.0f - nothing to post", min_score)
        db.event("warning", "post skipped: no ready clips")
        return None
    return _post_row(cfg, db, clip, dry_run=dry_run)


def post_clip(cfg, db: DB, clip_id: int, dry_run: bool = False) -> dict | None:
    clip = db.clip(clip_id)
    if not clip:
        raise RuntimeError(f"clip #{clip_id} not found")
    return _post_row(cfg, db, clip, dry_run=dry_run)


def _post_row(cfg, db: DB, clip: dict, dry_run: bool = False) -> dict:
    if not cfg.get("youtube.enabled", False):
        raise RuntimeError(
            "Automatic posting is disabled (youtube.enabled = false). "
            f"Upload the clip yourself from {cfg.output_dir}."
        )
    meta = read_json(clip.get("meta_path") or "", {}) or {}
    payload = _payload(clip, meta)

    if dry_run:
        LOG.info("[dry-run] would upload: %s", clip["path"])
        db.event("info", f"dry-run upload: {clip['path']}")
        return payload

    publish_at = None
    if cfg.get("youtube.schedule_uploads"):
        # YouTube requires publishAt to be in the future; give it a 15 min buffer.
        publish_at = (datetime.now().astimezone() + timedelta(minutes=15)).isoformat(timespec="seconds")

    LOG.info("uploading %s (score %.1f)", clip["path"], clip.get("score") or 0)
    try:
        result = youtube.upload_clip(
            cfg, clip["path"], payload["title"], payload["description"], payload["hashtags"], publish_at=publish_at
        )
    except youtube.QuotaExceeded as exc:
        # Keep it ready: the next slot can use it once the quota resets.
        db.execute(
            "INSERT INTO posts (clip_id, platform, status, detail, created_at) VALUES (?,?,?,?,?)",
            (clip["id"], "youtube", "quota", str(exc)[:900], now_iso()),
        )
        db.event("warning", f"quota reached, clip #{clip['id']} stays queued")
        notify(cfg, f"⏳ YouTube quota reached. Clip stays queued: {payload['title']}")
        raise
    except Exception as exc:  # noqa: BLE001
        attempts = int(clip.get("attempts") or 0) + 1
        status = "failed" if attempts >= 3 else "ready"
        db.update_clip(clip["id"], status=status, attempts=attempts, error=str(exc)[:900])
        db.execute(
            "INSERT INTO posts (clip_id, platform, status, detail, created_at) VALUES (?,?,?,?,?)",
            (clip["id"], "youtube", "failed", str(exc)[:900], now_iso()),
        )
        db.event("error", f"upload failed for clip #{clip['id']}: {exc}")
        notify(cfg, f"❌ Upload failed: {payload['title']}\n{str(exc)[:300]}")
        raise

    db.update_clip(clip["id"], status="posted", youtube_id=result["id"], posted_at=now_iso(), error=None)
    db.execute(
        "INSERT INTO posts (clip_id, platform, external_id, url, status, detail, created_at) VALUES (?,?,?,?,?,?,?)",
        (clip["id"], "youtube", result["id"], result["url"], "ok", result.get("privacy", ""), now_iso()),
    )
    db.event("info", f"posted clip #{clip['id']} -> {result['url']}")
    notify(cfg, f"✅ Posted: {payload['title']}\n{result['url']}\nScore {clip.get('score')}")
    LOG.info("posted: %s", result["url"])
    payload.update(result)
    return payload
