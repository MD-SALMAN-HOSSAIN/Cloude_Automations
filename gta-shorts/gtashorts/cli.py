from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import __version__, analyze, ffmpeg, faces, transcribe, youtube
from .assfile import Word, build_ass, write_ass
from .config import Config, load_config
from .db import DB
from .pipeline import Pipeline
from .poster import post_clip, post_next
from .scheduler import run_daemon
from .util import LOG, fmt_seconds, human_size, now_iso, read_json, setup_logging, slugify, write_json

SLOTS_DEFAULT = ["09:00", "13:00", "18:00", "21:00"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _db(cfg: Config) -> DB:
    return DB(cfg.data_dir / "shorts.db")


def _ready_count(cfg: Config, db: DB) -> int:
    return len(db.ready_clips(float(cfg.get("gemini.min_viral_score", 72))))


def _print_table(rows: list[list[str]], headers: list[str]) -> None:
    if not rows:
        print("  (nothing here yet)")
        return
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(line)
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        print("  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)))


def _next_slot(cfg: Config) -> tuple[str, str]:
    tz = ZoneInfo(cfg.timezone())
    now = datetime.now(tz)
    slots = cfg.get("scheduler.times", SLOTS_DEFAULT)
    for value in slots:
        hour, minute = (int(x) for x in value.split(":"))
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate > now:
            return value, candidate.strftime("%a %d %b %H:%M")
    hour, minute = (int(x) for x in slots[0].split(":"))
    tomorrow = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    from datetime import timedelta

    tomorrow += timedelta(days=1)
    return slots[0], tomorrow.strftime("%a %d %b %H:%M")


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

def cmd_doctor(cfg: Config, db: DB, args) -> int:
    ok = True
    print(f"\nGTA Shorts Automator v{__version__} - environment check\n" + "=" * 58)

    print(f"python      {sys.version.split()[0]}  ({sys.executable})")
    if sys.version_info < (3, 10):
        print("  ! Python 3.10+ is required")
        ok = False

    try:
        ff = ffmpeg.ffmpeg_bin()
        info = ffmpeg.run([ff, "-version"]).stdout.splitlines()[0]
        print(f"ffmpeg      {info[:70]}")
    except Exception as exc:  # noqa: BLE001
        print(f"ffmpeg      MISSING ({exc})")
        ok = False
    try:
        pb = ffmpeg.ffprobe_bin()
        print(f"ffprobe     {pb}")
    except Exception as exc:  # noqa: BLE001
        print(f"ffprobe     MISSING ({exc})")
        ok = False

    for module in ("yt_dlp", "google.genai", "faster_whisper", "cv2", "apscheduler", "googleapiclient"):
        try:
            __import__(module)
            print(f"package     {module:<22} ok")
        except Exception as exc:  # noqa: BLE001
            print(f"package     {module:<22} MISSING ({exc})")
            ok = False

    key = cfg.gemini_key()
    if key:
        print(f"gemini key  present (…{key[-4:]})")
        if args.deep:
            try:
                from google import genai

                client = genai.Client(api_key=key)
                response = client.models.generate_content(
                    model=cfg.get("gemini.model", "gemini-3.5-flash"), contents="Reply with the single word OK"
                )
                print(f"gemini live {(response.text or '').strip()[:40]}")
            except Exception as exc:  # noqa: BLE001
                print(f"gemini live FAILED: {str(exc)[:300]}")
                ok = False
    else:
        print("gemini key  MISSING - put GEMINI_API_KEY in .env (free at aistudio.google.com/apikey)")
        ok = False

    wanted = str(cfg.get("whisper.model", "base"))
    cached = sorted(
        {
            p.parent.parent.parent.name.split("faster-whisper-")[-1]
            for p in cfg.models_dir.glob("models--*/snapshots/*/model.bin")
        }
    )
    if wanted in cached:
        print(f"whisper     model cached ({wanted})")
    elif cached:
        print(f"whisper     '{wanted}' downloads on first video (cached: {', '.join(cached)})")
    else:
        print(f"whisper     model '{wanted}' downloads on first video (~80-150 MB)")

    yunet = cfg.models_dir / "face_detection_yunet_2023mar.onnx"
    print(f"face model  {'YuNet cached' if yunet.exists() else 'YuNet downloads on first video (~230 KB)'}")

    font = Path("C:/Windows/Fonts/impact.ttf")
    print(f"font        {'Impact found' if font.exists() else 'Impact missing (captions fall back to Arial)'}")

    if not cfg.get("youtube.enabled", False):
        print(f"posting     manual mode - clips are saved to {cfg.output_dir}")
    elif youtube.is_authorized(cfg):
        try:
            ch = youtube.channel_info(cfg)
            print(f"youtube     authorised: {ch['title']} ({ch['subscribers']} subs)")
        except Exception as exc:  # noqa: BLE001
            print(f"youtube     token present but unusable: {exc}")
            ok = False
    else:
        print("youtube     not authorised - run: shorts auth youtube")
        ok = False

    if cfg.get("youtube.enabled", False):
        secret = youtube.client_secret_path(cfg)
        print(f"oauth file  {'found' if secret.exists() else 'missing'} ({secret})")

    total, used, free = shutil.disk_usage(cfg.data_dir)
    print(f"disk free   {human_size(free)} of {human_size(total)}")

    counts = db.counts()
    ready = _ready_count(cfg, db)
    print(f"clips       {ready} saved, {counts['clips'].get('posted', 0)} posted total")
    if cfg.get("youtube.enabled", False):
        slot, when = _next_slot(cfg)
        print(f"next slot   {slot} ({when}), {cfg.timezone()}")
    else:
        print("posting     manual - upload from the Ready clips folder")
    print("=" * 58)
    print("READY\n" if ok else "NOT READY - fix the items marked above\n")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# add / process / run
# ---------------------------------------------------------------------------

def cmd_add(cfg: Config, db: DB, args) -> int:
    pipeline = Pipeline(cfg, db, stub=args.stub)
    for url in args.urls:
        try:
            ids = pipeline.add_url(url)
        except Exception as exc:  # noqa: BLE001
            print(f"! could not queue {url}: {exc}")
            continue
        if args.process:
            for source_id in ids:
                try:
                    pipeline.process_source(source_id, force=bool(getattr(args, "force", False)))
                except Exception as exc:  # noqa: BLE001
                    print(f"! source #{source_id} failed: {exc}")
    print(f"\n{_ready_count(cfg, db)} clip(s) saved to {cfg.output_dir}")
    if not args.process:
        print("queued only. Start the daemon or run: shorts process --next")
    return 0


def cmd_file(cfg: Config, db: DB, args) -> int:
    """Queue local recordings (your own gameplay) without a YouTube download."""
    pipeline = Pipeline(cfg, db, stub=args.stub)
    for raw in args.paths:
        path = Path(raw)
        try:
            source_id = pipeline.add_local_file(path)
        except Exception as exc:  # noqa: BLE001
            print(f"! could not queue {raw}: {exc}")
            continue
        print(f"queued source #{source_id}: {path.name}")
        if args.process:
            try:
                pipeline.process_source(source_id, force=bool(getattr(args, "force", False)))
            except Exception as exc:  # noqa: BLE001
                print(f"! source #{source_id} failed: {exc}")
    print(f"\n{_ready_count(cfg, db)} clip(s) saved to {cfg.output_dir}")
    return 0


def cmd_process(cfg: Config, db: DB, args) -> int:
    pipeline = Pipeline(cfg, db, stub=args.stub)
    force = bool(getattr(args, "force", False))
    if args.source:
        pipeline.process_source(int(args.source), force=force)
        return 0
    if args.all:
        while True:
            source = db.next_source()
            if not source:
                break
            try:
                pipeline.process_source(int(source["id"]), force=force)
            except Exception as exc:  # noqa: BLE001
                print(f"! source #{source['id']} failed: {exc}")
        return 0
    if args.next:
        source = db.next_source()
        if not source:
            print("nothing queued")
            return 0
        pipeline.process_source(int(source["id"]), force=force)
        return 0
    result = pipeline.refill(max_sources=int(args.sources))
    print(f"\nrefill done ({result} source(s) processed). {_ready_count(cfg, db)} clip(s) ready.")
    return 0


def cmd_run(cfg: Config, db: DB, args) -> int:
    pipeline = Pipeline(cfg, db, stub=args.stub)
    ids = pipeline.add_url(args.url)
    for source_id in ids:
        try:
            pipeline.process_source(source_id, force=bool(getattr(args, "force", False)))
        except Exception as exc:  # noqa: BLE001
            print(f"! source #{source_id} failed: {exc}")
    if args.post:
        if not cfg.get("youtube.enabled", False):
            print("automatic posting is off (manual mode) - upload the clips from the Ready clips folder")
        else:
            try:
                result = post_next(cfg, db, dry_run=args.dry_run)
                if result:
                    print(f"posted: {result.get('url', result['path'])}")
            except Exception as exc:  # noqa: BLE001
                print(f"! upload failed: {exc}")
                return 1
    print(f"\n{_ready_count(cfg, db)} clip(s) ready.")
    print(f"saved in: {cfg.output_dir}")
    return 0


# ---------------------------------------------------------------------------
# queue / status / posts
# ---------------------------------------------------------------------------

def cmd_queue(cfg: Config, db: DB, args) -> int:
    clips = db.ready_clips(float(args.min_score))
    print(f"{len(clips)} ready clip(s)  (buffer target {cfg.get('scheduler.buffer_target')})\n")
    rows = []
    for clip in clips:
        rows.append(
            [
                clip["id"],
                f"{clip['score']:.1f}" if clip.get("score") is not None else "-",
                f"{clip['duration']:.1f}s" if clip.get("duration") else "-",
                (clip.get("title") or "")[:58],
                Path(clip["path"]).name,
            ]
        )
    _print_table(rows, ["id", "score", "length", "title", "file"])
    return 0


def cmd_show(cfg: Config, db: DB, args) -> int:
    clip = db.clip(int(args.clip_id))
    if not clip:
        print(f"clip #{args.clip_id} not found")
        return 1
    meta = {}
    if clip.get("meta_path") and Path(clip["meta_path"]).exists():
        meta = read_json(Path(clip["meta_path"])) or {}

    score = meta.get("score") or {}
    print(f"\nclip #{clip['id']}  [{clip['status']}]\n" + "=" * 58)
    print(f"title       {clip.get('title') or '-'}")
    print(f"hook        {meta.get('hook') or '-'}")
    print(f"score       final {clip.get('score')}  (AI {score.get('ai', '-')}, measured {score.get('measured', '-')})")
    print(f"length      {fmt_seconds(clip.get('duration') or 0)}   {clip.get('width')}x{clip.get('height')}")
    print(f"file        {clip['path']}")
    print(f"thumbnail   {clip.get('thumb_path') or '-'}")
    if clip.get("youtube_id"):
        print(f"youtube     https://youtube.com/shorts/{clip['youtube_id']}")
    checks = meta.get("checks") or {}
    if checks:
        print("\nchecks")
        for name, value in checks.items():
            mark = "ok  " if value else "FAIL"
            print(f"  {mark} {name}")
    if meta.get("description"):
        print(f"\ndescription\n{meta['description']}")
    if meta.get("hashtags"):
        print("\nhashtags    " + " ".join(meta["hashtags"]))
    source = meta.get("source") or {}
    if source.get("url"):
        print(
            f"\nsource      {source.get('url')}  "
            f"({float(source.get('start') or 0):.1f}s - {float(source.get('end') or 0):.1f}s)"
        )
    if meta.get("reason"):
        print(f"why         {meta['reason']}")
    print("=" * 58 + "\n")
    return 0


def cmd_status(cfg: Config, db: DB, args) -> int:
    counts = db.counts()
    ready = _ready_count(cfg, db)
    target = int(cfg.get("scheduler.buffer_target", 12))
    manual = not cfg.get("youtube.enabled", False)
    print("\nGTA Shorts status\n" + "=" * 58)
    if manual:
        print(f"clips saved         {ready}")
        print(f"output folder       {cfg.output_dir}")
        print(f"gemini key          {'yes' if cfg.gemini_key() else 'NO'}")
        detail = ", ".join(f"{k}:{v}" for k, v in sorted(counts["sources"].items())) or "-"
        print(f"sources             {detail}")
    else:
        print(f"ready to post       {ready} / {target} target")
        for table in ("sources", "clips", "posts"):
            detail = ", ".join(f"{k}:{v}" for k, v in sorted(counts[table].items())) or "-"
            print(f"{table:<19} {detail}")
        today = datetime.now().date().isoformat()
        print(f"posted today        {db.posts_today(today)}")
        slot, when = _next_slot(cfg)
        print(f"next slot           {slot} ({when}, {cfg.timezone()})")
        print(f"gemini key          {'yes' if cfg.gemini_key() else 'NO'}")
        print(f"youtube auth        {'yes' if youtube.is_authorized(cfg) else 'NO'}")
        if ready < target:
            print("\nnote: buffer is low. The daemon will refill from queued sources; add more with: shorts add <link>")
    recent = db.query("SELECT created_at, level, message FROM events ORDER BY id DESC LIMIT 8")
    if recent:
        print("\nrecent events")
        for row in recent:
            print(f"  {row['created_at'][11:16]}  {row['level']:<7} {row['message'][:90]}")
    print("=" * 58 + "\n")
    return 0


def cmd_post(cfg: Config, db: DB, args) -> int:
    if not cfg.get("youtube.enabled", False):
        print("Automatic posting is OFF (manual mode).")
        print(f"Your clips are here - upload them yourself:\n  {cfg.output_dir}")
        return 0
    try:
        if args.clip:
            result = post_clip(cfg, db, int(args.clip), dry_run=args.dry_run)
        else:
            result = post_next(cfg, db, min_score=args.min_score, dry_run=args.dry_run)
    except youtube.AuthRequired as exc:
        print(f"! {exc}")
        return 2
    except youtube.QuotaExceeded as exc:
        print(f"! {exc}")
        return 3
    except Exception as exc:  # noqa: BLE001
        print(f"! upload failed: {exc}")
        return 1
    if not result:
        print("nothing to post - queue is empty or every clip is below the score threshold")
        return 1
    if result.get("url"):
        print(f"posted: {result['url']}")
    else:
        print(f"dry-run ok: {result['path']}")
    return 0


def cmd_auth(cfg: Config, db: DB, args) -> int:
    if args.target != "youtube":
        print("only 'youtube' is supported")
        return 1
    try:
        channel = youtube.authorize(cfg)
    except Exception as exc:  # noqa: BLE001
        print(f"! {exc}")
        return 1
    print(f"authorised channel: {channel['title']} ({channel['subscribers']} subscribers)")
    return 0


def cmd_logs(cfg: Config, db: DB, args) -> int:
    rows = db.query("SELECT created_at, level, message FROM events ORDER BY id DESC LIMIT ?", (int(args.lines),))
    for row in reversed(rows):
        print(f"{row['created_at']}  {row['level']:<7} {row['message']}")
    return 0


def cmd_retry(cfg: Config, db: DB, args) -> int:
    clips = db.execute("UPDATE clips SET status='ready', attempts=0, error=NULL WHERE status='failed'")
    sources = db.execute("UPDATE sources SET status='new', attempts=0, error=NULL WHERE status='failed'")
    print(f"reset {clips} clip(s) and {sources} source(s) for another try")
    return 0


# ---------------------------------------------------------------------------
# daemon
# ---------------------------------------------------------------------------

def cmd_daemon(cfg: Config, db: DB, args) -> int:
    if not cfg.get("youtube.enabled", False):
        print("Manual mode: there is nothing to schedule.")
        print("Just paste a link with Add Link.cmd and the finished clips appear in:")
        print(f"  {cfg.output_dir}")
        return 0
    if not cfg.gemini_key() and not args.stub:
        print("! GEMINI_API_KEY is missing. Add it to .env first (see README step 3).")
        return 2
    run_daemon(cfg, db, stub=args.stub)
    return 0


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------

def _check(name: str, condition: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if condition else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")
    return condition


def _white_centroid(png: Path) -> float | None:
    try:
        import numpy as np
        from PIL import Image

        arr = np.asarray(Image.open(png).convert("L"))
        ys, xs = np.nonzero(arr > 200)
        return float(xs.mean()) if len(xs) else None
    except Exception:
        return None


def cmd_selftest(cfg: Config, db: DB, args) -> int:
    from .ffmpeg import crop_geometry, make_test_video, make_thumbnail, path_expression, probe, render_clip, RenderRequest
    from .ffmpeg import copy_font

    print("\nGTA Shorts self-test\n" + "=" * 58)
    cfg.set("video.preset", "veryfast")
    test_dir = cfg.data_dir / "selftest"
    test_dir.mkdir(parents=True, exist_ok=True)
    video = test_dir / "source.mp4"
    if not video.exists() or args.fresh:
        make_test_video(video, 26.0, "1280x720")
    info = probe(video)
    passed = True
    passed &= _check("synthetic source", info.duration > 24 and info.width == 1280, f"{info.width}x{info.height} {info.duration:.1f}s")

    words = [
        Word(start=1.0 + i * 1.15, end=1.0 + i * 1.15 + 0.55, text=w)
        for i, w in enumerate(("SELF TEST CAPTIONS RENDER PERFECTLY EVERY TIME VERTICAL FACE TRACKING WORKS") .split() * 3)
    ]
    ass_text = build_ass(words, "SELF TEST", 26.0, cfg)
    (test_dir / "captions.ass").write_text(ass_text, encoding="utf-8")
    passed &= _check("ASS captions generated", ass_text.count("Dialogue:") > 20, f"{ass_text.count('Dialogue:')} events")
    passed &= _check("hook card present", "Style: Hook" in ass_text and "SELF TEST" in ass_text)

    crop_w, crop_h = crop_geometry(info.width, info.height, 1080, 1920)
    passed &= _check("9:16 crop geometry", crop_w == 404 and crop_h == 720, f"{crop_w}x{crop_h}")

    expr = path_expression([0.0, 5.0, 10.0], [100.0, 600.0, 100.0])
    passed &= _check("moving crop expression", "if(lt(t" in expr, expr[:48] + "...")

    track = {
        "width": info.width,
        "height": info.height,
        "fps": info.fps,
        "sample_interval": 0.5,
        "samples": [
            {
                "t": i * 0.5,
                "face_x": 0.5 + 0.16 * ((i % 8) / 8 - 0.5) * 2,
                "face_y": 0.45,
                "face_w": 0.12,
                "face_conf": 0.9,
                "motion_x": 0.5,
                "motion_y": 0.5,
                "motion_conf": 0.4,
            }
            for i in range(int(info.duration * 2))
        ],
    }
    path_data = faces.build_path(track, 0.0, 26.0, crop_w, crop_h, cfg)
    passed &= _check("face track path", path_data["face_coverage"] > 0.5, f"coverage {path_data['face_coverage']:.0%}")

    fonts = test_dir / "fonts"
    copy_font(str(cfg.get("video.caption_font", "Impact")), fonts)
    out = test_dir / "selftest-clip.mp4"
    started = time.time()
    try:
        result = render_clip(
            RenderRequest(
                source=video,
                start=0.0,
                end=min(26.0, info.duration),
                out_path=out,
                captions_ass=test_dir / "captions.ass",
                fonts_dir=fonts,
                crop_x=path_data["x"],
                crop_y=path_data["y"],
                source_info=info,
                cfg=cfg,
            )
        )
        elapsed = time.time() - started
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL  render - {exc}")
        return 1

    rendered = probe(out)
    passed &= _check("render resolution", rendered.width == 1080 and rendered.height == 1920, f"{rendered.width}x{rendered.height}")
    passed &= _check("render duration", abs(rendered.duration - info.duration) < 0.5, f"{rendered.duration:.2f}s")
    passed &= _check("audio track", rendered.has_audio)
    passed &= _check("loudness normalised", bool(result.get("loudness_ok")))
    passed &= _check("file size sane", out.stat().st_size > 200_000, human_size(out.stat().st_size))
    passed &= _check("render speed", elapsed < 300, f"{elapsed:.1f}s")
    thumb = make_thumbnail(out, test_dir / "thumb.jpg")
    passed &= _check("thumbnail", thumb.exists() and thumb.stat().st_size > 5000)

    # --- dynamic crop: a moving subject must stay near the centre ------
    marker = test_dir / "marker.mp4"
    if not marker.exists():
        ffmpeg.run(
            [
                ffmpeg.ffmpeg_bin(), "-hide_banner", "-y", "-nostdin",
                "-f", "lavfi", "-i", "color=c=black:s=1280x720:r=30:d=4",
                "-f", "lavfi", "-i", "color=c=white:s=120x120:r=30:d=4",
                "-filter_complex", "[0:v][1:v]overlay=x='(t/4)*1100':y=300:shortest=1",
                "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(marker),
            ],
            timeout=180,
        )
    marker_info = probe(marker)
    marker_track = {
        "width": 1280,
        "height": 720,
        "fps": 30,
        "sample_interval": 0.25,
        "samples": [
            {
                "t": i * 0.25,
                "face_x": ((i * 0.25 / 4) * 1100 + 60) / 1280,
                "face_y": 0.5,
                "face_w": 0.09,
                "face_conf": 0.9,
                "motion_x": 0.5,
                "motion_y": 0.5,
                "motion_conf": 0.2,
            }
            for i in range(17)
        ],
    }
    marker_path = faces.build_path(marker_track, 0.0, 4.0, 404, 720, cfg)
    marker_out = test_dir / "marker-out.mp4"
    render_clip(
        RenderRequest(
            source=marker,
            start=0.0,
            end=4.0,
            out_path=marker_out,
            captions_ass=None,
            crop_x=marker_path["x"],
            crop_y=marker_path["y"],
            source_info=marker_info,
            cfg=cfg,
            quiet=True,
        )
    )
    offsets = []
    for at in (1.2, 2.8):
        frame = test_dir / f"marker-{at}.png"
        ffmpeg.run([ffmpeg.ffmpeg_bin(), "-hide_banner", "-y", "-ss", str(at), "-i", str(marker_out), "-frames:v", "1", str(frame)])
        centroid = _white_centroid(frame)
        if centroid is not None:
            offsets.append(abs(centroid - 540))
    passed &= _check(
        "moving subject stays centred",
        bool(offsets) and max(offsets) < 200,
        ", ".join(f"{o:.0f}px off" for o in offsets) or "subject not found",
    )

    analysis = analyze.select_moments(info, {"words": [w.__dict__ for w in words], "segments": []}, cfg, stub=True)
    passed &= _check("stub analyser", len(analysis) >= 1, f"{len(analysis)} moments")
    passed &= _check("hashtag sanitiser", all(h.startswith("#") for h in analysis[0].hashtags) and len(analysis[0].hashtags) == 5)

    # Gemini 3 sometimes answers with a bare JSON array instead of {"moments": [...]}.
    array_reply = '[{"start": 1, "end": 20, "title": "T", "hook": "H", "description": "D", "hashtags": ["#a"], "scores": {"safety": 10}}]'
    parsed = analyze._extract_json(array_reply)
    passed &= _check("gemini array reply parsed", len(parsed.get("moments") or []) == 1, f"{len(parsed.get('moments') or [])} moments")
    fenced = analyze._extract_json("```json\n{\"moments\": []}\n```\ntrailing prose")
    passed &= _check("gemini fenced reply parsed", fenced == {"moments": []}, "object extracted")

    if args.full:
        print("\n  full pipeline test (this downloads the tiny whisper model on first run)")
        cfg.set("whisper.model", "tiny")
        cfg.set("download.min_minutes", 0.4)
        cfg.set("gemini.min_viral_score", 0)
        cfg.set("video.max_clips_per_video", 1)
        cfg.set("video.target_max_seconds", 20)
        long_video = test_dir / "long-source.mp4"
        if not long_video.exists():
            make_test_video(long_video, 75.0, "640x360")
        pipeline = Pipeline(cfg, db, stub=True, fake_track=True)
        source_id = pipeline.add_local_file(long_video, "selftest full pipeline")
        before = len(db.ready_clips(0))
        pipeline.process_source(source_id)
        after = len(db.ready_clips(0))
        passed &= _check("full pipeline produced a clip", after > before, f"{after - before} new clip(s)")
        # Never let a synthetic test clip reach the post queue.
        for row in db.query("SELECT id, path FROM clips WHERE title LIKE 'Self-test clip%' AND status = 'ready'"):
            db.update_clip(row["id"], status="skipped")
            Path(row["path"]).unlink(missing_ok=True)

    print("=" * 58)
    print(f"SELFTEST {'PASSED' if passed else 'FAILED'}\n")
    return 0 if passed else 1


# ---------------------------------------------------------------------------
# startup shortcut
# ---------------------------------------------------------------------------

def cmd_startup(cfg: Config, db: DB, args) -> int:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        print("APPDATA is not set - cannot install the startup shortcut")
        return 1
    startup = Path(appdata) / "Microsoft/Windows/Start Menu/Programs/Startup"
    target = startup / "GTA-Shorts-Daemon.vbs"
    if args.action == "remove":
        target.unlink(missing_ok=True)
        print(f"removed {target}")
        return 0

    if not cfg.get("youtube.enabled", False):
        print("Manual mode - no background daemon is needed.")
        print("Paste a link with Add Link.cmd and the clips are saved to:")
        print(f"  {cfg.output_dir}")
        return 0

    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        pythonw = Path(sys.executable)
    vbs = (
        'Set sh = CreateObject("WScript.Shell")\r\n'
        f'sh.CurrentDirectory = "{cfg.root}"\r\n'
        f'sh.Run """{pythonw}"" -m gtashorts daemon", 0, False\r\n'
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(vbs, encoding="ascii")
    print(f"installed startup shortcut: {target}")
    print("The daemon now starts silently at every sign-in. Remove with: shorts startup remove")
    return 0


def cmd_cleanup(cfg: Config, db: DB, args) -> int:
    from .scheduler import cleanup_job

    cleanup_job(cfg, db)
    return 0


def cmd_open(cfg: Config, db: DB, args) -> int:
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    os.startfile(str(cfg.output_dir))
    print(cfg.output_dir)
    return 0


def cmd_config(cfg: Config, db: DB, args) -> int:
    print(json.dumps(cfg._data, indent=2))
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shorts",
        description="GTA Shorts Automator - turn a gameplay video into vertical Shorts saved on your PC.",
    )
    parser.add_argument("--config", help="path to config.toml")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="check the machine, keys and auth")
    p.add_argument("--deep", action="store_true", help="also make a live Gemini call")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("add", help="queue one or more YouTube links (and optionally process them now)")
    p.add_argument("urls", nargs="+")
    p.add_argument("--process", dest="process", action="store_true", default=True)
    p.add_argument("--no-process", dest="process", action="store_false")
    p.add_argument("--stub", action="store_true", help="offline analyser, for testing")
    p.add_argument("--force", action="store_true", help="re-render even if the link was already processed")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("file", help="queue your own local video files (no download)")
    p.add_argument("paths", nargs="+")
    p.add_argument("--process", dest="process", action="store_true", default=True)
    p.add_argument("--no-process", dest="process", action="store_false")
    p.add_argument("--stub", action="store_true")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_file)

    p = sub.add_parser("process", help="turn queued sources into clips")
    p.add_argument("--source", type=int)
    p.add_argument("--next", action="store_true")
    p.add_argument("--all", action="store_true")
    p.add_argument("--sources", type=int, default=3, help="max sources to process in this run")
    p.add_argument("--stub", action="store_true")
    p.add_argument("--force", action="store_true", help="re-render even if the source was already processed")
    p.set_defaults(func=cmd_process)

    p = sub.add_parser("run", help="add a link, make clips, optionally post the best one")
    p.add_argument("--url", required=True)
    p.add_argument("--post", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--stub", action="store_true")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("queue", help="list rendered clips waiting to post")
    p.add_argument("--min-score", type=float, default=0)
    p.set_defaults(func=cmd_queue)

    p = sub.add_parser("open", help="open the folder with your finished Shorts")
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("status", help="queue health, next slot, recent events")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("show", help="full scorecard + metadata for one clip")
    p.add_argument("clip_id", type=int)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("post", help="upload the next clip (or a specific one)")
    p.add_argument("--clip", type=int)
    p.add_argument("--min-score", type=float)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_post)

    p = sub.add_parser("auth", help="one-time YouTube sign-in")
    p.add_argument("target", choices=["youtube"], default="youtube")
    p.set_defaults(func=cmd_auth)

    p = sub.add_parser("daemon", help="run the scheduler (this is the automation)")
    p.add_argument("--stub", action="store_true")
    p.set_defaults(func=cmd_daemon)

    p = sub.add_parser("selftest", help="render a synthetic clip to prove the pipeline works")
    p.add_argument("--full", action="store_true", help="also run whisper + the full pipeline")
    p.add_argument("--fresh", action="store_true", help="regenerate the synthetic source")
    p.set_defaults(func=cmd_selftest)

    p = sub.add_parser("logs", help="show the event log")
    p.add_argument("--lines", type=int, default=30)
    p.set_defaults(func=cmd_logs)

    p = sub.add_parser("retry", help="reset failed sources and clips")
    p.set_defaults(func=cmd_retry)

    p = sub.add_parser("cleanup", help="delete old working files")
    p.set_defaults(func=cmd_cleanup)

    p = sub.add_parser("startup", help="start the daemon automatically at sign-in")
    p.add_argument("action", choices=["install", "remove"])
    p.set_defaults(func=cmd_startup)

    p = sub.add_parser("config", help="print the merged configuration")
    p.set_defaults(func=cmd_config)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    setup_logging(cfg.data_dir / "logs" / "shorts.log", verbose=args.verbose)
    db = _db(cfg)
    try:
        return int(args.func(cfg, db, args) or 0)
    except KeyboardInterrupt:
        print("\nstopped")
        return 130
    finally:
        db.close()
