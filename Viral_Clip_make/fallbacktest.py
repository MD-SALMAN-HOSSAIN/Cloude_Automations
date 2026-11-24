"""Regression test: an empty AI pick list must not abort a run.

Reproduces the 24 Nov 2026 failure where the picker answered ``[]`` and the
pipeline raised "No moment passed the viral-quality gate" with no clips made.
Run:
    python fallbacktest.py
No network or API key needed.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import ai, pipeline
from core.util import Logger

TRANSCRIPT = {
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
    "text": "Look at this police chase. He jumps the car. No way, he did it!",
}


class Cfg:
    def __init__(self, **overrides):
        self.data = {
            "ai.provider": "gemini", "ai.model": "test",
            "general.min_seconds": 8, "general.max_seconds": 22,
            "general.clips_per_video": 3,
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


def test_empty_ai_uses_local():
    original = ai.enhance_moments
    ai.enhance_moments = lambda *a, **kw: []
    try:
        log, lines = capture()
        picked = pipeline.select_moments(TRANSCRIPT, [], Cfg(), log)
    finally:
        ai.enhance_moments = original
    assert picked, "empty AI reply aborted the run instead of using local picks"
    assert any("falling back" in line.lower() for line in lines), lines
    print(f"PASS empty AI reply falls back to {len(picked)} local moment(s)")


def test_relaxed_last_resort():
    original_local, original_ai = pipeline.find_moments, ai.enhance_moments
    calls = []

    def local(*a, **kw):
        calls.append(bool(kw.get("relaxed")))
        return [] if not kw.get("relaxed") else [
            type("M", (), {"start": 0.0, "end": 12.0, "score": 40.0})()
        ]

    pipeline.find_moments = local
    ai.enhance_moments = lambda *a, **kw: []
    try:
        log, lines = capture()
        picked = pipeline.select_moments(TRANSCRIPT, [], Cfg(), log)
    finally:
        pipeline.find_moments, ai.enhance_moments = original_local, original_ai
    assert calls == [False, True], calls
    assert len(picked) == 1, picked
    assert any("relaxed local pass" in line.lower() for line in lines), lines
    print("PASS relaxed last-resort pass keeps the best window")


def test_nothing_usable_still_errors():
    original_local, original_ai = pipeline.find_moments, ai.enhance_moments
    pipeline.find_moments = lambda *a, **kw: []
    ai.enhance_moments = lambda *a, **kw: []
    try:
        log, _ = capture()
        try:
            pipeline.select_moments(TRANSCRIPT, [], Cfg(), log)
        except RuntimeError as exc:
            assert "No usable moment" in str(exc), exc
        else:
            raise AssertionError("a video with no candidates should report the problem")
    finally:
        pipeline.find_moments, ai.enhance_moments = original_local, original_ai
    print("PASS genuinely unusable source still reports a clear error")


def test_prompt_no_longer_invites_zero():
    prompt = ai._build_prompt(TRANSCRIPT, Cfg())
    assert "even ZERO" not in prompt, "prompt still tells the AI zero is fine"
    assert "at least one moment" in prompt, prompt
    print("PASS prompt asks for at least one moment")


if __name__ == "__main__":
    test_empty_ai_uses_local()
    test_relaxed_last_resort()
    test_nothing_usable_still_errors()
    test_prompt_no_longer_invites_zero()
    print("moment fallback: all checks passed")
