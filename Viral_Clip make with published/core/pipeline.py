from __future__ import annotations

import json
import re
from collections import namedtuple
from datetime import datetime
from pathlib import Path

from . import ai, faces, style
from .audio import decode_audio, rms_curve
from .captions import build_ass, write_ass
from .downloader import fetch
from .ffmpeg_tools import (crop_geometry, make_thumbnail, probe, render_clip,
                           RenderRequest)
from .moments import find_moments
from .transcribe import transcribe
from .util import Logger

Word = namedtuple("Word", "start end text")


def _slug(text: str, limit: int = 48) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", (text or "clip").lower()).strip("-")
    return (text[:limit] or "clip").strip("-")


def _report(progress, pct: float, message: str):
    if progress:
        progress(pct, message)


ASPECTS = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
    "3:4": (1080, 1440),
    "16:9": (1920, 1080),
}


def _apply_aspect(cfg, log: Logger):
    aspect = str(cfg.get("video.aspect", "9:16") or "9:16")
    if aspect == "custom":
        width = int(cfg.get("video.custom_width", 1080))
        height = int(cfg.get("video.custom_height", 1920))
    else:
        width, height = ASPECTS.get(aspect, ASPECTS["9:16"])
    width, height = max(2, width - width % 2), max(2, height - height % 2)
    cfg.set("video.width", width)
    cfg.set("video.height", height)
    log.info("Output aspect: %s (%dx%d)", aspect, width, height)
    return width, height


def run_pipeline(source: str, cfg, log: Logger, progress=None) -> dict:
    """Full local pipeline: source -> scored 9:16 clips ready to upload."""
    # 1. Resolve the source --------------------------------------------------
    _report(progress, 2, "Resolving video source...")
    video = fetch(source, cfg, log)
    width, height = _apply_aspect(cfg, log)

    # 2. Inspect -------------------------------------------------------------
    _report(progress, 6, "Inspecting video...")
    info = probe(video)
    if info.duration < 3:
        raise RuntimeError("Video is too short to make clips from.")
    max_seconds = float(cfg.get("general.max_minutes", 45)) * 60
    analysis_seconds = min(info.duration, max_seconds)
    log.info("Source: %dx%d, %.1f s ( analysing %.1f s )",
             info.width, info.height, info.duration, analysis_seconds)

    # 3. Decode audio + transcribe ------------------------------------------
    _report(progress, 10, "Decoding audio locally...")
    audio = decode_audio(video, analysis_seconds)

    _report(progress, 14, "Transcribing with local Whisper model...")
    if audio.size > 0:
        transcript = transcribe(video, cfg, log, audio=audio)
    else:
        log.warn("No audio stream detected - scoring on motion only")
        transcript = {"words": [], "segments": [], "duration": analysis_seconds,
                      "text": "", "language": None}

    # 4. Score moments locally ----------------------------------------------
    _report(progress, 46, "Scoring viral moments locally (no API)...")
    rms = rms_curve(audio)
    moments = find_moments(transcript, rms, cfg, log)
    ai_moments = ai.enhance_moments(transcript, moments, cfg, log)
    if ai_moments:
        moments = ai_moments
    if not moments:
        raise RuntimeError(
            "No suitable moments were found. Try a video with speech and more energy."
        )

    # 5. Framing / subject tracking -----------------------------------------
    framing = str(cfg.get("video.framing", "auto") or "auto").strip().lower()
    if framing not in ("auto", "fit_blur", "fit_black", "track"):
        framing = "auto"
    track = None
    crop_w = crop_h = 0
    # Auto mode measures the subject so the AI / heuristic can choose a style.
    # Turning face tracking off is still respected: auto then prefers whole frame.
    need_track = (bool(cfg.get("features.face_track", True))
                  and framing in ("auto", "track"))
    if need_track:
        _report(progress, 53, "Tracking the subject for smart framing...")
        cache = cfg.cache_dir / (video.stem + ".track.json")
        track = faces.analyse_video(
            video, cfg, log, cache_path=cache,
            sample_interval=float(cfg.get("speed.face_interval", 0.5)),
            max_seconds=analysis_seconds,
        )
    elif framing == "track":
        log.info("Face tracking off - using centre crop")
    elif framing == "auto":
        log.info("Face tracking off - auto style will prefer the whole frame")
    if framing in ("track", "auto"):
        crop_w, crop_h = crop_geometry(info.width, info.height, width, height)
    else:
        log.info("Framing: whole video visible in %s (no content cut, no face lock)",
                 "a blurred vertical canvas" if framing == "fit_blur" else "black bars")

    decisions = style.choose_styles(moments, track, cfg, log, info=info)

    # 6. Render each clip ----------------------------------------------------
    out_root = Path(str(cfg.get("general.output_dir")))
    out_root.mkdir(parents=True, exist_ok=True)
    day_dir = out_root / datetime.now().strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%H%M%S")
    results = []
    total = len(moments)

    for index, moment in enumerate(moments, 1):
        pct = 60 + (34.0 * (index - 1) / max(1, total))
        _report(progress, pct, f"Rendering clip {index} of {total}...")
        name = f"{stamp}-{index:02d}-{_slug(moment.title)}"
        out_path = day_dir / (name + ".mp4")
        ass_path = day_dir / (name + ".ass")

        window_words = [Word(float(w["start"]), float(w["end"]), w["text"])
                        for w in transcript.get("words", [])
                        if moment.start <= float(w["start"]) < moment.end]
        relative = [Word(w.start - moment.start, w.end - moment.start, w.text)
                    for w in window_words]

        if bool(cfg.get("features.captions", True)) and relative:
            ass_text = build_ass(relative, moment.hook, moment.end - moment.start,
                                 cfg, show_hook=bool(cfg.get("features.hook", True)))
            write_ass(ass_path, ass_text)
        else:
            ass_path = None

        decision = decisions[index - 1]
        clip_framing = decision.framing
        crop_x = crop_y = None
        if track and crop_w and clip_framing == "track":
            path = faces.build_path(
                track, moment.start, moment.end, crop_w, crop_h, cfg,
                focus_x=decision.focus_x, focus_y=decision.focus_y,
            )
            crop_x, crop_y = path["x"], path["y"]

        req = RenderRequest(
            source=video, start=moment.start, end=moment.end,
            out_path=out_path, captions_ass=ass_path,
            crop_x=crop_x, crop_y=crop_y, source_info=info, cfg=cfg,
            framing=clip_framing,
        )
        render_info = render_clip(req, log=log)
        make_thumbnail(out_path, day_dir / (name + ".jpg"),
                       at=min(1.5, (moment.end - moment.start) / 3), width=width)

        txt = (
            f"{moment.title}\n"
            f"{'=' * len(moment.title)}\n\n"
            f"Viral score : {moment.score}/100 (local signal engine)\n"
            f"Source time : {moment.start:.1f}s - {moment.end:.1f}s\n"
            f"Hook        : {moment.hook}\n"
            f"Signals     : {moment.reason}\n"
            f"Clip style  : {style.STYLE_LABELS.get(clip_framing, clip_framing)} "
            f"({decision.source}: {decision.reason})\n"
            f"Source      : {source}\n\n"
            f"Description : {moment.title} — full video on the channel.\n\n"
            f"#shorts #viral #clip #{_slug(moment.title).replace('-', '')}\n"
        )
        (day_dir / (name + ".txt")).write_text(txt, encoding="utf-8")
        (day_dir / (name + ".json")).write_text(
            json.dumps({"moment": moment.__dict__, "render": render_info,
                        "style": decision.as_dict(),
                        "source": str(video), "made_with": "Viral Clip Make (100% free)"},
                       indent=2),
            encoding="utf-8",
        )
        if ass_path and ass_path.exists():
            try:
                ass_path.unlink()
            except Exception:
                pass

        results.append({
            "name": name,
            "path": str(out_path),
            "title": moment.title,
            "score": moment.score,
            "duration": round(moment.end - moment.start, 1),
            "style": clip_framing,
            "style_reason": decision.reason,
        })
        log.ok("Clip %d/%d ready: %s (%s/100, %s)", index, total, name,
               moment.score, style.STYLE_LABELS.get(clip_framing, clip_framing))

    if bool(cfg.get("upload.auto_publish", False)):
        from .upload_post import publish_clips

        _report(progress, 97, "Publishing clips to social media...")
        try:
            upload_results = publish_clips(
                [Path(c["path"]) for c in results], cfg, log,
                titles=[c["title"] for c in results],
                on_progress=lambda pct, msg: _report(progress, 97 + pct * 2, msg),
            )
            for clip, upload_result in zip(results, upload_results):
                clip["upload"] = upload_result
        except Exception as exc:
            log.warn("Auto-publish failed: %s", exc)

    _report(progress, 97, "Done - finishing up...")
    return {
        "output_dir": str(day_dir),
        "clips": results,
        "source_video": str(video),
        "language": transcript.get("language"),
    }
