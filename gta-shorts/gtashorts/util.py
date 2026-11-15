from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Sequence

LOG = logging.getLogger("gtashorts")

_LOGGER_READY = False


def setup_logging(log_file: Path | None = None, verbose: bool = False) -> None:
    global _LOGGER_READY
    level = logging.DEBUG if verbose else logging.INFO
    if _LOGGER_READY:
        LOG.setLevel(level)
        return
    LOG.setLevel(level)
    LOG.propagate = False

    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%H:%M:%S")

    console = logging.StreamHandler(stream=sys.stdout)
    console.setFormatter(fmt)
    LOG.addHandler(console)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        LOG.addHandler(file_handler)
    _LOGGER_READY = True


def log(msg: str, *args) -> None:
    LOG.info(msg, *args)


def run(cmd: Sequence[str], cwd: Path | None = None, timeout: int | None = None, check: bool = False) -> subprocess.CompletedProcess:
    """Run a subprocess, capturing text output. Never uses a shell."""
    cmd = [str(c) for c in cmd]
    LOG.debug("exec: %s", " ".join(cmd))
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if proc.returncode != 0 and check:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-12:]
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(cmd[:3])} ...\n" + "\n".join(tail))
    return proc


def which(name: str) -> str | None:
    # 1. explicit override
    env_key = f"{name.upper()}_PATH"
    override = os.environ.get(env_key)
    if override and Path(override).exists():
        return override
    # 2. PATH
    found = shutil.which(name)
    if found:
        return found
    # 3. common Windows install locations
    home = Path.home()
    candidates = [
        home / "AppData/Local/Microsoft/WinGet/Links" / f"{name}.exe",
        home / "AppData/Local/Programs" / name / f"bin/{name}.exe",
        Path("C:/ffmpeg/bin") / f"{name}.exe",
        Path("C:/Program Files/ffmpeg/bin") / f"{name}.exe",
        Path("C:/ProgramData/chocolatey/bin") / f"{name}.exe",
    ]
    for cand in candidates:
        if cand.exists():
            return str(cand)
    # 3b. winget installs packages into a hashed folder; find the binary there
    packages = home / "AppData/Local/Microsoft/WinGet/Packages"
    if packages.exists():
        for sub in ("Gyan.FFmpeg", "yt-dlp", "ffmpeg"):
            for cand in packages.glob(f"{sub}*/**/bin/{name}.exe"):
                return str(cand)
        for cand in packages.glob(f"**/{name}.exe"):
            return str(cand)
    # 4. tools/ folder shipped with the project
    tools = Path(__file__).resolve().parent.parent / "tools"
    if tools.exists():
        for cand in tools.rglob(f"{name}.exe"):
            return str(cand)
    return None


def slugify(text: str, max_len: int = 48) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (text[:max_len].strip("-") or "clip")


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def parse_hhmm(value: str) -> tuple[int, int]:
    hour, minute = value.strip().split(":")
    return int(hour), int(minute)


def fmt_seconds(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    return f"{int(seconds // 60)}:{seconds % 60:04.1f}"


def human_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:3.1f}{unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f}PB"


def retry(fn, attempts: int = 3, delay: float = 2.0, what: str = "operation"):
    last = None
    for i in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if i == attempts:
                break
            wait = delay * (2 ** (i - 1))
            LOG.warning("%s failed (attempt %d/%d): %s - retrying in %.0fs", what, i, attempts, exc, wait)
            time.sleep(wait)
    raise last  # type: ignore[misc]


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
