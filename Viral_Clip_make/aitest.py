"""Test the optional AI provider path with a local mock API server."""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.ai import enhance_moments
from core.config import Config
from core.util import Logger

PAYLOAD = [
    {"start": 1.0, "end": 9.0, "title": "The AI Picked This Moment",
     "hook": "WATCH THIS NOW", "reason": "strong hook", "score": 91},
]


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        envelope = {"choices": [{"message": {"content": json.dumps(PAYLOAD)}}]}
        body = json.dumps(envelope).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_port

    cfg = Config()
    cfg.set("ai.provider", "openai")
    cfg.set("ai.base_url", f"http://127.0.0.1:{port}/v1")
    cfg.set("ai.model", "mock-model")
    cfg.set("ai.api_key", "test-key")
    cfg.set("general.min_seconds", 8)
    cfg.set("general.max_seconds", 12)
    cfg.set("general.clips_per_video", 2)
    transcript = {
        "duration": 20.0,
        "segments": [{"start": 0.0, "end": 10.0, "text": "hello there"},
                     {"start": 10.0, "end": 20.0, "text": "and welcome"}],
        "words": [{"start": i, "end": i + 0.5, "text": "word"} for i in range(20)],
        "text": "hello there and welcome",
    }
    log = Logger(lambda level, msg: print(f"[{level}] {msg}"))
    picked = enhance_moments(transcript, [], cfg, log)
    assert picked, "AI returned no moments"
    assert picked[0].title == "The AI Picked This Moment", picked[0]
    assert picked[0].hook == "WATCH THIS NOW", picked[0]
    print("AI TEST PASSED:", picked[0])
    server.shutdown()


if __name__ == "__main__":
    main()
