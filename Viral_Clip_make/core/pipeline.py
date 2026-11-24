from __future__ import annotations

import json
import re
from collections import namedtuple
from datetime import datetime
from pathlib import Path

from . import ai, cleanup, faces, style
from .audio import decode_audio, rms_curve
from .captions import build_ass, write_ass
from .downloader import fetch
from .ffmpeg_tools import (crop_geometry, facecam_layout, make_thumbnail, probe,
                           render_clip, RenderRequest)
from .layout import split_boxes
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


def select_moments(transcript: dict, rms: list, cfg, log: Logger,
                   progress=None) -> list:
    """Combine AI and local picks without letting an empty answer abort the run.

    Fallback order: AI picks -> strict local picks -> relaxed local picks.
    A spoiler source is still rejected outright; everything else should yield
    clips rather than a traceback.
    """
    provider = str(cfg.get("ai.provider", "local") or "local").lower()
    if provider == "local":
        _report(progress, 46, "Scoring viral moments locally (no API)...")
    else:
        _report(progress, 46, f"AI ({provider}) is choosing the strongest moments...")

    moments = find_moments(transcript, rms, cfg, log)
    ai_moments = ai.enhance_moments(transcript, moments, cfg, log, rms=rms)
    # None = API failed / skipped: use the local picks. [] = the picker answered
    # with nothing usable. Never make that fatal while local candidates exist -
    # a strict prompt or a model returning an empty array must not abort the run.
    if ai_moments:
        moments = ai_moments
    elif ai_moments is not None and moments:
        log.warn("AI returned no usable picks - falling back to the local moment picker")
    if not moments:
        # Last resort: keep the best windows even if the strict premise /
        # standalone checks found nothing. Spoilers stay excluded.
        log.warn("Strict gate found no moments - trying a relaxed local pass")
        moments = find_moments(transcript, rms, cfg, log, relaxed=True)
    if not moments:
        raise RuntimeError(
            "No usable moment found in this video. Try a clip with visible action "
            "and spoken reactions (not a spoiler recap, interview or narration-only video)."
        )
    return moments


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
    """Full local pipeline: source -> scored vertical clips ready to watch."""
    # 1. Resolve the source --------------------------------------------------
    _report(progress, 2, "Resolving video source...")
    video = fetch(source, cfg, log, progress=progress)
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

    # 4. Score moments -------------------------------------------------------
    rms = rms_curve(audio)
    moments = select_moments(transcript, rms, cfg, log, progress)

    # 5. Game-subject framing -----------------------------------------------
    track = None
    crop_w = crop_h = 0
    split_on = bool(cfg.get("features.split_layout", False))
    facecam_on = split_on or bool(cfg.get("features.facecam_pip", False))
    facecam = None
    webcam_rect = None
    split_boxes_px = None
    # The game character is the main subject no matter what. Tracking can never
    # be switched off by a stale setting: without a track there is no crop, and
    # without a crop the character disappears into the whole-frame background.
    log.info("Game-subject lock: tracking the character for every clip")
    _report(progress, 53, "Tracking the game character for smart framing...")
    cache = cfg.cache_dir / (video.stem + ".track.json")
    track = faces.analyse_video(
        video, cfg, log, cache_path=cache,
        sample_interval=float(cfg.get("speed.face_interval", 0.5)),
        max_seconds=analysis_seconds,
    )
    corner = str(cfg.get("video.facecam_corner", "auto") or "auto").lower()
    webcam_rect = faces.find_facecam(track, corner=corner) if track else None
    if split_on:
        # Corner webcam first, otherwise a persistent face at the top of frame.
        facecam = webcam_rect or (faces.find_speaker_facecam(track, corner=corner)
                                  if track else None)
        if facecam:
            split_boxes_px = split_boxes(cfg, width, height)
            log.ok("Split layout: speaker fills the top panel, gameplay tracked below")
        else:
            log.warn("Split layout needs a detected face - using the normal gameplay crop")
            split_on = False
            facecam_on = False
            cfg.set("features.split_layout", False)  # keep captions on the single frame too
    elif facecam_on:
        if webcam_rect:
            facecam = webcam_rect
            log.ok("Facecam found in the %s corner - webcam goes in a circle, character stays centred",
                   facecam["corner"])
        else:
            log.warn("Facecam option is on but no corner webcam was detected - using normal framing")
    elif webcam_rect:
        log.info("Webcam bubble detected - excluded from the crop so the character stays dominant")

    ignore_rect = facecam if split_on else webcam_rect

    # Always crop: the character fills the clip (or the gameplay panel in a
    # split layout) instead of sharing it with the minimap, HUD and background.
    if split_on:
        gp = split_boxes_px["gameplay"]
        crop_w, crop_h = crop_geometry(info.width, info.height, gp["w"], gp["h"])
    else:
        crop_w, crop_h = crop_geometry(info.width, info.height, width, height)
    subject_track = track or {
        "width": info.width, "height": info.height,
        "samples": [], "sample_interval": 0.5,
    }

    if provider != "local" and bool(cfg.get("ai.auto_style", True)):
        _report(progress, 58, f"AI ({provider}) is locating the character in each clip...")
    decisions = style.choose_styles(moments, track, cfg, log, info=info,
                                    ignore_rect=ignore_rect)

    # 6. Render each clip ----------------------------------------------------
    out_root = Path(str(cfg.get("general.output_dir")))
    out_root.mkdir(parents=True, exist_ok=True)
    day_dir = out_root / datetime.now().strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%H%M%S")
    results = []
    total = len(moments)
    hook_margin = None
    if split_on:
        # Hook sits in the facecam panel; captions own the gameplay panel.
        hook_margin = split_boxes_px["top_bar"] + int(
            split_boxes_px["facecam"]["h"] * 0.05)
    elif facecam:
        # Keep the hook below the circle instead of behind it.
        bubble = facecam_layout(cfg, facecam, width, height)
        hook_margin = bubble["y"] + bubble["size"] + max(16, int(width * 0.035))

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
                                 cfg, show_hook=bool(cfg.get("features.hook", True)),
                                 hook_margin=hook_margin)
            write_ass(ass_path, ass_text)
        else:
            ass_path = None

        decision = decisions[index - 1]
        # style.choose_styles already guarantees this; keep the hard lock here
        # too so a future edit cannot silently reintroduce whole-frame clips.
        clip_framing = "track"
        focus_x, focus_y = decision.focus_x, decision.focus_y
        if facecam:
            # The speaker is in their own panel; the character owns the rest.
            focus_x, focus_y = 0.5, 0.45
        crop_x = crop_y = None
        if crop_w and crop_h:
            path = faces.build_path(
                subject_track, moment.start, moment.end, crop_w, crop_h, cfg,
                focus_x=focus_x, focus_y=focus_y, ignore_rect=webcam_rect,
            )
            crop_x, crop_y = path["x"], path["y"]

        req = RenderRequest(
            source=video, start=moment.start, end=moment.end,
            out_path=out_path, captions_ass=ass_path,
            crop_x=crop_x, crop_y=crop_y, source_info=info, cfg=cfg,
            framing=clip_framing, facecam=facecam, split_layout=split_on,
        )
        try:
            render_info = render_clip(req, log=log)
            make_thumbnail(out_path, day_dir / (name + ".jpg"),
                           at=min(1.5, (moment.end - moment.start) / 3), width=width)

            txt = (
                f"{moment.title}\n"
                f"{'=' * len(moment.title)}\n\n"
                f"Viral score : {moment.score}/100 ("
                f"{provider + ' AI pick' if provider != 'local' else 'local signal engine'})\n"
                f"Source time : {moment.start:.1f}s - {moment.end:.1f}s\n"
                f"Hook        : {moment.hook}\n"
                f"Signals     : {moment.reason}\n"
                f"Clip style  : {style.STYLE_LABELS.get(clip_framing, clip_framing)} "
                f"({decision.source}: {decision.reason})\n"
                f"Main subject: {decision.subject} (locked, {decision.locked})\n"
                + (f"Layout      : split screen - speaker top / gameplay bottom\n"
                   if split_on else
                   (f"Layout      : facecam bubble in top {facecam['corner']} corner, "
                    f"subject centred\n" if facecam else ""))
                + f"Source      : {source}\n\n"
                f"Description : {moment.title} — full video on the channel.\n\n"
                f"#shorts #viral #clip #{_slug(moment.title).replace('-', '')}\n"
            )
            (day_dir / (name + ".txt")).write_text(txt, encoding="utf-8")
            (day_dir / (name + ".json")).write_text(
                json.dumps({"moment": moment.__dict__, "render": render_info,
                            "style": decision.as_dict(),
                            "facecam": facecam if facecam else None,
                            "layout": "split" if split_on else "single",
                            "source": str(video), "made_with": "Viral Clip Make (100% free)"},
                           indent=2),
                encoding="utf-8",
            )
        except Exception:
            # A half-rendered clip is useless - delete it and its sidecars now.
            cleanup.remove_clip_artifacts(out_path, ass_path, log)
            raise
        finally:
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
            "layout": "split" if split_on else "single",
        })
        log.ok("Clip %d/%d ready: %s (%s/100, %s)", index, total, name,
               moment.score, style.STYLE_LABELS.get(clip_framing, clip_framing))

    _report(progress, 97, "Done - finishing up...")
    return {
        "output_dir": str(day_dir),
        "clips": results,
        "source_video": str(video),
        "language": transcript.get("language"),
    }
