from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    _NO_WINDOW = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
except AttributeError:  # non-Windows
    _NO_WINDOW = 0


def clamp(value, low, high):
    return max(low, min(high, value))


def run(cmd, cwd=None, timeout=None, text=True):
    """Run a subprocess quietly (no console flash in the packaged .exe)."""
    kwargs = dict(
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
        timeout=timeout,
        text=text,
    )
    if text:
        kwargs.update(encoding="utf-8", errors="replace")
    if os.name == "nt":
        kwargs["creationflags"] = _NO_WINDOW
    return subprocess.run(cmd, **kwargs)


def which(name: str):
    return shutil.which(name)


def app_base_dir() -> Path:
    """Folder that contains the application (source project or bundled exe)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


class Logger:
    """Simple leveled logger that forwards messages to the GUI."""

    def __init__(self, callback=None):
        self.callback = callback

    def _emit(self, level: str, message: str, *args):
        try:
            text = message % args if args else message
        except Exception:
            text = " ".join([message] + [str(a) for a in args])
        if self.callback:
            self.callback(level, text)

    def info(self, message, *args):
        self._emit("INFO", message, *args)

    def warn(self, message, *args):
        self._emit("WARN", message, *args)

    def error(self, message, *args):
        self._emit("ERROR", message, *args)

    def ok(self, message, *args):
        self._emit("OK", message, *args)
