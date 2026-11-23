"""Regression test for the viral clip picker.

Reproduces the 22 Nov 2026 failure: Gemini returned 10 ending/spoiler recaps
from a GTA 6 lore video and every one was exported with a 90+ score. Run:
    python viralchoicetest.py
No network and no API key needed.
"""
from __future__ import annotations

import glob
import json
from pathlib import Path

from core import ai, moments
from core.util import Logger

REAL_TRANSCRIPT = Path(
    r"C:\Users\msi\AppData\Roaming\ViralClipMake\downloads"
    r"\source_7963ae8aaeb3b45a.mp4.transcript.json"
)
BAD_OUTPUT = Path(r"D:\Processed Clip\2026-11-22")


class Cfg:
    def __init__(self, **overrides):
        self.data = {
            "general.min_seconds": 12,
            "general.max_seconds": 45,
            "general.clips_per_video": 10,
            "moments.ai_weight": 0.6,
            "ai.provider": "gemini",
            "ai.model": "test",
        }
        self.data.update(overrides)

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


def capture():
    lines = []

    def log(level, text):
        lines.append(f"{level}: {text}")

    return Logger(log), lines


def test_retention_guard():
    cfg = Cfg(**{"general.min_seconds": 40, "general.max_seconds": 50})
    assert moments.window_limits(cfg) == (22.0, 35.0), moments.window_limits(cfg)
    print("PASS retention guard: 40-50s -> 22-35s")


def test_boundary_bonus():
    quiet = moments._boundary_bonus([0.0] * 20, 2.0, 0.25, 0.05, 100, True)
    loud = moments._boundary_bonus([0.5] * 20, 2.0, 0.25, 0.05, 100, True)
    assert quiet == 1.0 and loud == 0.0, (quiet, loud)
    print("PASS silence boundary now beats loud boundary")


def test_end_snap():
    segs = [{"start": 0, "end": 5}, {"start": 5, "end": 10}, {"start": 10, "end": 15}]
    assert ai._snap(11.2, segs, edge="end") == 10.0
    assert ai._snap(11.2, segs, edge="start") == 10.0
    print("PASS clip ends snap to sentence ends")


def test_real_failure_is_rejected():
    if not REAL_TRANSCRIPT.exists() or not BAD_OUTPUT.exists():
        print("SKIP real-run regression (output/transcript not present)")
        return
    transcript = json.loads(REAL_TRANSCRIPT.read_text(encoding="utf-8"))
    bad = []
    for path in sorted(BAD_OUTPUT.glob("*.json")):
        m = json.loads(path.read_text(encoding="utf-8"))["moment"]
        # Old AI replies had only a single uncalibrated score and no flags.
        bad.append({
            "start": m["start"], "end": m["end"], "title": m["title"],
            "hook": m["hook"], "reason": m["reason"], "score": m["score"],
        })
    assert len(bad) == 10, len(bad)

    original = ai._call_gemini
    ai._call_gemini = lambda prompt, cfg: json.dumps(bad)
    try:
        log, lines = capture()
        cfg = Cfg(**{
            "general.min_seconds": 40, "general.max_seconds": 50,
            "general.clips_per_video": 10,
        })
        picked = ai.enhance_moments(transcript, [], cfg, log, rms=[])
    finally:
        ai._call_gemini = original

    assert picked == [], picked
    assert any("spoiler" in line.lower() for line in lines), lines
    print(f"PASS all 10 spoiler/ending picks rejected "
          f"({len([x for x in lines if 'Rejected' in x])} gate logs)")


def test_good_pick_passes_and_is_blended():
    transcript = {
        "duration": 22.0,
        "segments": [
            {"start": 0.0, "end": 4.0, "text": "Look at this police chase outside the KFC."},
            {"start": 8.0, "end": 12.0, "text": "He jumps the car and lands on the roof."},
            {"start": 16.0, "end": 22.0, "text": "No way, he actually did it!"},
        ],
        "words": [
            {"start": 0.0, "end": 0.4, "text": "Look"},
            {"start": 0.4, "end": 0.8, "text": "at"},
            {"start": 0.8, "end": 1.2, "text": "this"},
            {"start": 1.2, "end": 1.7, "text": "police"},
            {"start": 1.7, "end": 2.2, "text": "chase"},
            {"start": 8.0, "end": 8.5, "text": "He"},
            {"start": 8.5, "end": 9.0, "text": "jumps"},
            {"start": 9.0, "end": 9.6, "text": "the"},
            {"start": 9.6, "end": 10.2, "text": "car"},
            {"start": 16.0, "end": 16.4, "text": "No"},
            {"start": 16.4, "end": 16.9, "text": "way"},
            {"start": 16.9, "end": 17.5, "text": "he"},
            {"start": 17.5, "end": 18.2, "text": "did"},
            {"start": 18.2, "end": 18.7, "text": "it"},
        ],
    }
    good = [{
        "start": 0.0, "end": 22.0,
        "title": "Kai Cenat reacts to a GTA 6 police chase",
        "hook": "KAI CENAT CAR CHAOS",
        "reason": "visible payoff",
        "premise": True, "payoff_in_last_40pct": True, "picture_first": True,
        "visual_payoff": True, "standalone": True, "spoiler": False,
        "scores": {"premise": 9, "payoff": 9, "hook": 8,
                   "picture": 8, "standalone": 9, "safety": 10},
    }]
    original = ai._call_gemini
    ai._call_gemini = lambda prompt, cfg: json.dumps(good)
    try:
        log, lines = capture()
        picked = ai.enhance_moments(
            transcript, [], Cfg(**{"general.clips_per_video": 3}), log, rms=[]
        )
    finally:
        ai._call_gemini = original

    assert len(picked) == 1, picked
    assert 0 < picked[0].score < 100, picked[0].score
    assert "spoiler" not in " ".join(lines).lower(), lines
    print(f"PASS good moment kept with blended score {picked[0].score}/100")


if __name__ == "__main__":
    test_retention_guard()
    test_boundary_bonus()
    test_end_snap()
    test_real_failure_is_rejected()
    test_good_pick_passes_and_is_blended()
    print("viral choice logic: all checks passed")
