"""Viral Clip Make - 100% free, fully local viral-shorts maker.

Run with:  python app.py
Build exe: run build_exe.bat (or build.ps1)
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _write_crash(exc_text: str):
    try:
        import os
        base = os.environ.get("APPDATA") or str(Path.home())
        log = Path(base) / "ViralClipMake" / "crash.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(exc_text, encoding="utf-8")
    except Exception:
        pass


def main():
    if "--doctor" in sys.argv or "--selftest" in sys.argv:
        from core.diagnostics import doctor, selftest
        if "--doctor" in sys.argv:
            doctor()
        if "--selftest" in sys.argv:
            selftest()
        return
    try:
        from gui.main_window import launch
        launch()
    except Exception:
        text = traceback.format_exc()
        _write_crash(text)
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                "Viral Clip Make - startup error",
                text[-1200:] + "\n\nA copy was saved to %APPDATA%\\ViralClipMake\\crash.log",
            )
        except Exception:
            print(text)


if __name__ == "__main__":
    main()
