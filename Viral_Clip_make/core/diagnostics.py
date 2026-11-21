"""Hidden command-line diagnostics for the packaged .exe (writes a report file)."""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

from .config import app_data_dir
from .util import Logger


def _report_file(name: str) -> Path:
    path = app_data_dir() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def doctor() -> Path:
    from .ffmpeg_tools import ffmpeg_bin, ffprobe_bin

    lines = [
        "Viral Clip Make - doctor report",
        f"frozen      : {getattr(sys, 'frozen', False)}",
        f"MEIPASS     : {getattr(sys, '_MEIPASS', '')}",
        f"executable  : {sys.executable}",
    ]
    for label, fn in (("ffmpeg", ffmpeg_bin), ("ffprobe", ffprobe_bin)):
        try:
            lines.append(f"{label:<12}: {fn()}")
        except Exception as exc:
            lines.append(f"{label:<12}: ERROR - {exc}")
    lines.append(f"app data    : {app_data_dir()}")
    try:
        from .hardware import summary_line

        lines.append(f"hardware    : {summary_line()}")
    except Exception as exc:
        lines.append(f"hardware    : ERROR - {exc}")
    out = _report_file("doctor.txt")
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return out


def api_check() -> Path:
    from .ai import test_connection
    from .config import Config

    cfg = Config()
    status = test_connection(cfg)
    lines = [
        "Viral Clip Make - API connection check",
        f"provider : {status['provider']}",
        f"model    : {status['model']}",
        f"ok       : {status['ok']}",
        f"latency  : {status['latency_ms']} ms",
        f"message  : {status['message']}",
    ]
    out = _report_file("api_check.txt")
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return out


def selftest() -> Path:
    path = _report_file("selftest_result.txt")
    try:
        import tempfile

        from .config import Config
        from .ffmpeg_tools import make_test_video
        from .pipeline import run_pipeline

        cfg = Config()
        cfg.set("whisper.model", "tiny")
        cfg.set("general.min_seconds", 8)
        cfg.set("general.max_seconds", 14)
        cfg.set("general.clips_per_video", 1)
        cfg.set("general.max_minutes", 1)
        work = Path(tempfile.mkdtemp(prefix="vcm_exe_selftest_"))
        cfg.set("general.output_dir", str(work / "output"))
        source = make_test_video(work / "source.mp4", seconds=20)
        log = Logger(lambda level, msg: None)
        result = run_pipeline(str(source), cfg, log)
        lines = ["SELFTEST PASSED",
                 f"clips: {len(result['clips'])}",
                 f"output: {result['output_dir']}"]
        for clip in result["clips"]:
            assert Path(clip["path"]).exists(), "missing clip file"
    except Exception:
        lines = ["SELFTEST FAILED", traceback.format_exc()]
    path.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return path
