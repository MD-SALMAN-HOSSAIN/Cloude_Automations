"""Probe which Gemini models actually respond for the saved key (fast, tiny output)."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.config import Config
from google import genai
from google.genai import types

CANDIDATES = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
]


def main():
    key = str(Config().get("ai.api_key", "")).strip()
    client = genai.Client(api_key=key)
    cfg = types.GenerateContentConfig(max_output_tokens=8)
    for name in CANDIDATES:
        started = time.perf_counter()
        try:
            client.models.generate_content(model=name, contents="Reply with OK", config=cfg)
            print(f"OK    {name}  {int((time.perf_counter()-started)*1000)} ms", flush=True)
        except Exception as exc:
            msg = " ".join(str(exc).split())[:110]
            print(f"FAIL  {name}  {msg}", flush=True)


if __name__ == "__main__":
    main()
