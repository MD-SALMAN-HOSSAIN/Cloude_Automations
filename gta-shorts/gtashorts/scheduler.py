from __future__ import annotations

import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .db import DB
from .pipeline import Pipeline
from .poster import post_next
from .util import LOG, parse_hhmm


def _now(tz) -> datetime:
    return datetime.now(tz)


def _slot_times(cfg, day: datetime) -> list[datetime]:
    tz = day.tzinfo
    out = []
    for value in cfg.get("scheduler.times", ["09:00", "13:00", "18:00", "21:00"]):
        hour, minute = parse_hhmm(value)
        out.append(day.replace(hour=hour, minute=minute, second=0, microsecond=0, tzinfo=tz))
    return sorted(out)


def _last_post_age_minutes(db: DB, tz) -> float | None:
    last = db.last_post_time()
    if not last:
        return None
    try:
        posted = datetime.fromisoformat(last)
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=tz)
        return (_now(tz) - posted).total_seconds() / 60
    except Exception:
        return None


def post_job(cfg, db: DB, slot: str = "") -> None:
    tz = ZoneInfo(cfg.timezone())
    gap = float(cfg.get("scheduler.min_gap_minutes", 150))
    age = _last_post_age_minutes(db, tz)
    if age is not None and age < gap:
        LOG.info("slot %s skipped: last post was %.0f min ago (minimum gap %.0f)", slot, age, gap)
        db.event("info", f"slot {slot} skipped, last post {age:.0f} min ago")
        return
    try:
        result = post_next(cfg, db)
        if result:
            LOG.info("slot %s posted %s", slot, result.get("url", ""))
    except Exception as exc:  # noqa: BLE001
        LOG.error("slot %s failed: %s", slot, exc)


def refill_job(cfg, db: DB, pipeline: Pipeline) -> None:
    try:
        target = int(cfg.get("scheduler.buffer_target", 12))
        ready = len(db.ready_clips(float(cfg.get("gemini.min_viral_score", 72))))
        LOG.info("refill check: %d/%d clips ready", ready, target)
        pipeline.refill(target)
    except Exception as exc:  # noqa: BLE001
        LOG.error("refill failed: %s", exc)
        db.event("error", f"refill failed: {exc}")


def cleanup_job(cfg, db: DB) -> None:
    days = float(cfg.get("scheduler.cleanup_days", 7))
    cutoff = time.time() - days * 86400
    for folder in (cfg.work_dir, cfg.data_dir / "rejected"):
        if not folder.exists():
            continue
        for child in folder.iterdir():
            try:
                if child.stat().st_mtime < cutoff:
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink(missing_ok=True)
            except Exception:
                pass
    db.event("info", "cleanup finished")
    LOG.info("cleanup finished (working files older than %.0f days removed)", days)


def catch_up(cfg, db: DB) -> None:
    if not cfg.get("scheduler.catch_up", True):
        return
    tz = ZoneInfo(cfg.timezone())
    now = _now(tz)
    slots = _slot_times(cfg, now)
    passed = [slot for slot in slots if slot <= now]
    if not passed:
        return
    posted_today = db.posts_today(now.date().isoformat())
    if posted_today >= len(passed):
        return
    gap = float(cfg.get("scheduler.min_gap_minutes", 150))
    age = _last_post_age_minutes(db, tz)
    if age is not None and age < gap:
        LOG.info("catch-up skipped: last post %.0f min ago", age)
        return
    LOG.info("catch-up: %d slot(s) passed today, %d post(s) made - posting one now", len(passed), posted_today)
    try:
        post_next(cfg, db)
    except Exception as exc:  # noqa: BLE001
        LOG.error("catch-up upload failed: %s", exc)


def run_daemon(cfg, db: DB, stub: bool = False) -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    tz = ZoneInfo(cfg.timezone())
    pipeline = Pipeline(cfg, db, stub=stub)
    scheduler = BlockingScheduler(
        timezone=tz,
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600},
    )

    slots = cfg.get("scheduler.times", ["09:00", "13:00", "18:00", "21:00"])
    posting = bool(cfg.get("youtube.enabled", False))
    if posting:
        for value in slots:
            hour, minute = parse_hhmm(value)
            scheduler.add_job(
                lambda slot=value: post_job(cfg, db, slot),
                CronTrigger(hour=hour, minute=minute, timezone=tz),
                id=f"post-{value.replace(':', '')}",
                replace_existing=True,
            )

    scheduler.add_job(
        lambda: refill_job(cfg, db, pipeline),
        "interval",
        minutes=int(cfg.get("scheduler.refill_minutes", 20)),
        id="refill",
        next_run_time=_now(tz) + timedelta(seconds=10),
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: cleanup_job(cfg, db),
        CronTrigger(hour=3, minute=30, timezone=tz),
        id="cleanup",
        replace_existing=True,
    )

    LOG.info("=" * 62)
    LOG.info("GTA Shorts daemon running (timezone %s)", cfg.timezone())
    if posting:
        LOG.info("posting at: %s", ", ".join(slots))
    else:
        LOG.info("posting: manual (clips are saved to disk)")
    LOG.info("buffer target: %s clips | refill every %s min", cfg.get("scheduler.buffer_target"), cfg.get("scheduler.refill_minutes"))
    LOG.info("=" * 62)

    if posting:
        catch_up(cfg, db)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        LOG.info("daemon stopped")
    finally:
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass
