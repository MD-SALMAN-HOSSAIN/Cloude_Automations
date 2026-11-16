from __future__ import annotations

import json
import shutil
import threading
from dataclasses import replace
from datetime import date
from pathlib import Path

from . import analyze, download, faces, transcribe
from .assfile import Word, build_ass, write_ass
from .db import DB
from .ffmpeg import MediaInfo, RenderRequest, copy_font, crop_geometry, make_thumbnail, probe, render_clip
from .util import LOG, clamp, now_iso, read_json, slugify, write_json


class Pipeline:
    def __init__(self, cfg, db: DB, stub: bool = False, fake_track: bool = False):
        self.cfg = cfg
        self.db = db
        self.stub = stub
        self.fake_track = fake_track
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Queueing
    # ------------------------------------------------------------------
    def add_url(self, url: str) -> list[int]:
        info = download.inspect(url, self.cfg)
        items = info.get("playlist") or [info]
        ids: list[int] = []
        for item in items:
            if item.get("is_live"):
                LOG.warning("skipping live stream: %s", item.get("title"))
                continue
            existing = self.db.one(
                "SELECT id, status FROM sources WHERE url = ? ORDER BY id DESC LIMIT 1", (item["url"],)
            )
            if existing and existing["status"] == "done":
                LOG.info("already processed, skipping: %s", item.get("title"))
                continue
            source_id = self.db.add_source(item["url"])
            self.db.update_source(
                source_id,
                video_id=item.get("video_id"),
                title=item.get("title"),
                duration=item.get("duration") or 0,
            )
            LOG.info("queued source #%d: %s (%.0fs)", source_id, item.get("title"), item.get("duration") or 0)
            ids.append(source_id)
        self.db.event("info", f"queued {len(ids)} source(s) from {url}")
        return ids

    def add_local_file(self, path: Path, title: str = "") -> int:
        path = Path(path).resolve()
        if not path.exists():
            raise FileNotFoundError(path)
        info = probe(path)
        source_id = self.db.add_source(f"file:///{path.as_posix()}")
        self.db.update_source(
            source_id,
            video_id=path.stem,
            title=title or path.stem,
            duration=info.duration,
            path=str(path),
        )
        return source_id

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------
    def refill(self, target: int | None = None, max_sources: int = 3) -> int:
        if not self._lock.acquire(blocking=False):
            LOG.info("refill skipped: a processing job is already running")
            return 0
        try:
            target = int(target or self.cfg.get("scheduler.buffer_target", 12))
            min_score = float(self.cfg.get("gemini.min_viral_score", 72))
            processed = 0
            while len(self.db.ready_clips(min_score)) < target and processed < max_sources:
                source = self.db.next_source()
                if not source:
                    LOG.info("refill: no more sources queued")
                    break
                try:
                    self.process_source(int(source["id"]))
                except Exception as exc:  # noqa: BLE001
                    LOG.error("source #%s failed: %s", source["id"], exc)
                processed += 1
            return processed
        finally:
            self._lock.release()

    def process_source(self, source_id: int, force: bool = False) -> dict:
        source = self.db.source(source_id)
        if not source:
            raise RuntimeError(f"source #{source_id} not found")

        # Pasting the same link twice should not re-render and duplicate clips.
        if not force and source.get("status") == "done":
            done = self.db.query(
                "SELECT id FROM clips WHERE source_id = ? AND status = 'ready' ORDER BY id", (source_id,)
            )
            if done:
                LOG.info(
                    "source #%d already processed (%d clip(s)) - skipping. Use --force to redo it.",
                    source_id, len(done),
                )
                return {"source_id": source_id, "clips": [int(c["id"]) for c in done], "skipped": True}

        attempts = int(source.get("attempts") or 0) + 1
        self.db.update_source(source_id, status="processing", attempts=attempts, error=None)
        LOG.info("=== processing source #%d: %s", source_id, source.get("title") or source.get("url"))

        try:
            path = Path(source["path"]) if source.get("path") and Path(source["path"]).exists() else None
            if path is None:
                if str(source["url"]).startswith("file:///"):
                    path = Path(str(source["url"])[8:])
                    if not path.exists():
                        raise RuntimeError(f"local file disappeared: {path}")
                else:
                    downloaded = download.download(source["url"], self.cfg, source.get("video_id"))
                    path = Path(downloaded["path"])
                    source["title"] = source.get("title") or downloaded.get("title")
                    self.db.update_source(source_id, path=str(path), title=source["title"])

            info = probe(path)
            min_minutes = float(self.cfg.get("download.min_minutes", 2))
            if info.duration < min_minutes * 60:
                raise RuntimeError(
                    f"source is only {info.duration:.0f}s long; need at least {min_minutes:.0f} min "
                    "to cut good Shorts"
                )
            self.db.update_source(source_id, path=str(path), duration=info.duration, title=source.get("title") or path.stem)

            # Long VODs: cap everything (transcript, face track, moment picking) to
            # download.max_minutes so a 3-hour stream does not take 3 hours to analyse.
            limit_minutes = float(self.cfg.get("download.max_minutes", 0) or 0)
            if limit_minutes and info.duration > limit_minutes * 60:
                LOG.info(
                    "source is %.0f min long; analysing only the first %.0f min",
                    info.duration / 60, limit_minutes,
                )
                info = replace(info, duration=limit_minutes * 60)

            transcript = transcribe.transcribe(path, self.cfg)
            if not transcript.get("words"):
                LOG.warning("no speech detected - moments will be chosen from motion/audio only")

            track = self._track(path, info)
            moments = analyze.select_moments(
                info, transcript, self.cfg, title=source.get("title") or path.stem, stub=self.stub
            )
            if not moments:
                raise RuntimeError("no candidate moments found")

            made: list[int] = []
            for moment in moments:
                try:
                    clip_id = self._render_moment(source, path, info, transcript, track, moment)
                    if clip_id:
                        made.append(clip_id)
                except Exception as exc:  # noqa: BLE001
                    LOG.error("clip %.1f-%.1f failed: %s", moment.start, moment.end, exc)
                    self.db.event("error", f"clip render failed {moment.start:.1f}-{moment.end:.1f}: {exc}")

            self.db.update_source(source_id, status="done", error=None)
            self.db.event("info", f"source #{source_id}: {len(made)} clip(s) ready")
            LOG.info("=== source #%d done: %d clip(s)", source_id, len(made))
            return {"source_id": source_id, "clips": made}

        except Exception as exc:  # noqa: BLE001
            self.db.update_source(source_id, status="failed", error=str(exc)[:1000])
            self.db.event("error", f"source #{source_id} failed: {exc}")
            LOG.error("source #%d failed: %s", source_id, exc)
            raise

    def _track(self, path: Path, info: MediaInfo) -> dict:
        if self.fake_track:
            samples = []
            t = 0.0
            while t < 3600:
                # a face wandering left/right keeps the crop expression exercised
                samples.append(
                    {
                        "t": t,
                        "face_x": 0.5 + 0.18 * ((t % 6) / 6 - 0.5) * 2,
                        "face_y": 0.45,
                        "face_w": 0.1,
                        "face_conf": 0.9,
                        "motion_x": 0.5,
                        "motion_y": 0.5,
                        "motion_conf": 0.4,
                    }
                )
                t += 0.5
            return {
                "width": info.width,
                "height": info.height,
                "fps": info.fps,
                "sample_interval": 0.5,
                "samples": samples,
            }
        cache = self.cfg.cache_dir / f"{path.stem}.faces.json"
        limit = float(self.cfg.get("download.max_minutes", 0) or 0) * 60
        return faces.analyse_video(path, self.cfg, cache_path=cache, max_seconds=limit)

    # ------------------------------------------------------------------
    # One clip
    # ------------------------------------------------------------------
    def _render_moment(
        self,
        source: dict,
        path: Path,
        info: MediaInfo,
        transcript: dict,
        track: dict,
        moment: analyze.Moment,
    ) -> int | None:
        cfg = self.cfg
        width = int(cfg.get("video.width", 1080))
        height = int(cfg.get("video.height", 1920))
        crop_w, crop_h = crop_geometry(info.width, info.height, width, height)

        candidate_id = self.db.add_candidate(
            source_id=int(source["id"]),
            start=moment.start,
            end=moment.end,
            ai_score=moment.ai_score,
            title=moment.title,
            hook=moment.hook,
            description=moment.description,
            hashtags=json.dumps(moment.hashtags),
            scores_json=json.dumps(moment.scores),
            reason=moment.reason,
        )

        work = cfg.work_dir / f"src{source['id']}-c{candidate_id}"
        work.mkdir(parents=True, exist_ok=True)
        fonts = work / "fonts"
        copy_font(str(cfg.get("video.caption_font", "Impact")), fonts)

        clip_words = [
            Word(
                start=max(0.0, float(w["start"]) - moment.start),
                end=max(0.06, float(w["end"]) - moment.start),
                text=str(w["text"]),
            )
            for w in (transcript.get("words") or [])
            if moment.start - 0.05 <= float(w["start"]) < moment.end
        ]
        clip_words.sort(key=lambda w: w.start)

        ass_path = work / "captions.ass"
        write_ass(ass_path, build_ass(clip_words, moment.hook, moment.duration, cfg))

        path_data = faces.build_path(track, moment.start, moment.end, crop_w, crop_h, cfg)
        if self.fake_track:
            path_data = {
                "x": (list(path_data["x"][0]), list(path_data["x"][1])),
                "y": ([0.0], [0.0]),
                "face_coverage": 0.9,
                "motion_coverage": 0.5,
                "samples": path_data.get("samples", 10),
            }

        out_path = work / "clip.mp4"
        result = render_clip(
            RenderRequest(
                source=path,
                start=moment.start,
                end=moment.end,
                out_path=out_path,
                captions_ass=ass_path,
                fonts_dir=fonts,
                crop_x=path_data["x"],
                crop_y=path_data["y"],
                source_info=info,
                cfg=cfg,
            )
        )

        measured, checks = self._score(moment, clip_words, path_data, result, info, cfg)
        ai_weight = float(cfg.get("scoring.ai_weight", 0.60))
        final_score = round(ai_weight * moment.ai_score + (1 - ai_weight) * measured, 1)
        min_score = float(cfg.get("gemini.min_viral_score", 72))
        LOG.info(
            "clip %.1f-%.1f  ai=%.0f measured=%.0f final=%.1f  face=%.0f%%",
            moment.start, moment.end, moment.ai_score, measured, final_score, path_data["face_coverage"] * 100,
        )

        thumb = make_thumbnail(out_path, work / "thumb.jpg", at=min(1.5, moment.duration / 2))
        meta = {
            "title": moment.title,
            "hook": moment.hook,
            "description": moment.description,
            "hashtags": moment.hashtags,
            "score": {"ai": moment.ai_score, "measured": measured, "final": final_score},
            "checks": checks,
            "reason": moment.reason,
            "source": {
                "id": source["id"],
                "url": source["url"],
                "title": source.get("title"),
                "video_id": source.get("video_id"),
                "start": moment.start,
                "end": moment.end,
            },
            "technical": result,
            "created_at": now_iso(),
            "platform": "youtube",
        }

        # Only genuinely unsafe moments are withheld (YouTube advertiser guidelines).
        safety = float((moment.scores or {}).get("safety", 10) or 0)
        if safety and safety <= 2:
            rejected = self.cfg.data_dir / "rejected"
            rejected.mkdir(parents=True, exist_ok=True)
            slug = f"{date.today().isoformat()}-{int(moment.start)}-{slugify(moment.title, 32)}"
            shutil.move(str(out_path), str(rejected / f"{slug}.mp4"))
            self.db.update_candidate(candidate_id, status="rejected", final_score=final_score)
            self.db.event("warning", f"unsafe clip withheld (safety {safety:.0f}/10): {moment.title}")
            shutil.rmtree(work, ignore_errors=True)
            return None

        if final_score < min_score:
            # Manual mode: save everything the AI shortlisted and let the user decide.
            LOG.info(
                "clip %.1f-%.1f scored %.1f (< %.0f) - saving anyway for manual review",
                moment.start, moment.end, final_score, min_score,
            )

        slug = f"{date.today().isoformat()}-{int(moment.start):05d}-{slugify(moment.title, 40)}"
        dest_dir = self.cfg.output_dir / date.today().isoformat()
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{slug}.mp4"
        counter = 2
        while dest.exists():
            dest = dest_dir / f"{slug}-{counter}.mp4"
            counter += 1

        shutil.move(str(out_path), str(dest))
        if thumb.exists():
            shutil.move(str(thumb), str(dest.with_suffix(".jpg")))
        meta_path = dest.with_suffix(".json")
        write_json(meta_path, meta)
        dest.with_suffix(".txt").write_text(_post_text(meta, moment), encoding="utf-8")

        clip_id = self.db.add_clip(
            candidate_id=candidate_id,
            source_id=int(source["id"]),
            path=str(dest),
            meta_path=str(meta_path),
            thumb_path=str(dest.with_suffix(".jpg")),
            duration=result["duration"],
            width=result["width"],
            height=result["height"],
            score=final_score,
            title=moment.title,
            description=moment.description,
            hashtags=json.dumps(moment.hashtags),
            status="ready",
        )
        self.db.update_candidate(candidate_id, status="rendered", final_score=final_score)
        self.db.event("info", f"clip ready: {dest.name} (score {final_score})")
        shutil.rmtree(work, ignore_errors=True)
        return clip_id

    def _score(self, moment, words, path_data, result, info, cfg) -> tuple[float, dict]:
        dur = max(0.1, moment.duration)
        tmin = float(cfg.get("video.target_min_seconds", 22))
        tmax = float(cfg.get("video.target_max_seconds", 38))
        centre = (tmin + tmax) / 2
        spread = max(1.0, tmax - tmin)
        duration_score = clamp(100 - abs(dur - centre) / spread * 70, 0, 100)

        face_cov = float(path_data.get("face_coverage", 0))
        face_score = clamp(face_cov / 0.6, 0, 1) * 100

        wps = len(words) / dur
        max_wps = float(cfg.get("scoring.max_words_per_sec", 4.6))
        if wps <= max_wps:
            readability = 100 if wps >= 1.6 else clamp(55 + wps / 1.6 * 45, 0, 100)
        else:
            readability = clamp(100 - (wps - max_wps) * 25, 0, 100)

        motion_score = clamp(float(path_data.get("motion_coverage", 0)) / 0.45, 0, 1) * 100
        audio_score = 100 if (result.get("loudness_ok") and info.has_audio) else (55 if info.has_audio else 10)
        hook_words = len((moment.hook or "").split())
        hook_score = 100 if 1 <= hook_words <= 5 else (55 if hook_words else 0)
        first_word = min((w.start for w in words), default=99)
        tight_start = 100 if first_word <= 0.7 else clamp(100 - (first_word - 0.7) * 40, 0, 100)

        measured = round(
            duration_score * 0.14
            + face_score * 0.26
            + readability * 0.14
            + motion_score * 0.18
            + audio_score * 0.10
            + hook_score * 0.10
            + tight_start * 0.08,
            1,
        )

        checks = {
            "hook_first_second": hook_score >= 55,
            "captions_word_level": len(words) >= 5,
            "face_centred": face_cov >= float(cfg.get("scoring.min_face_coverage", 0.35)),
            "duration_in_range": float(cfg.get("video.clip_min_seconds", 15)) <= dur <= float(cfg.get("video.clip_max_seconds", 55)),
            "duration_ideal": tmin <= dur <= tmax,
            "loudness_normalised": bool(result.get("loudness_ok")),
            "caption_readable": wps <= max_wps,
            "hashtags_ready": len(moment.hashtags) >= 3,
            "vertical_1080x1920": result["width"] == 1080 and result["height"] == 1920,
            "tight_start": tight_start >= 70,
            "score_parts": {
                "duration": round(duration_score, 1),
                "face": round(face_score, 1),
                "readability": round(readability, 1),
                "action": round(motion_score, 1),
                "audio": round(audio_score, 1),
                "hook": round(hook_score, 1),
            },
        }
        return measured, checks


def _post_text(meta: dict, moment) -> str:
    tags = " ".join(meta["hashtags"])
    return (
        f"TITLE\n{meta['title']}\n\n"
        f"HOOK (burned in video)\n{meta['hook']}\n\n"
        f"DESCRIPTION\n{meta['description']}\n{tags}\n\n"
        f"HASHTAGS\n{tags}\n\n"
        f"SCORE\nAI {meta['score']['ai']}  /  measured {meta['score']['measured']}  /  final {meta['score']['final']}\n\n"
        f"SOURCE\n{meta['source']['url']}  ({moment.start:.1f}s - {moment.end:.1f}s)\n"
    )
