"""Test the AI connection-status checker against a mock OpenAI-compatible API."""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.ai import test_connection
from core.config import Config


class OK(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.dumps({"choices": [{"message": {"content": "OK"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class FAIL(BaseHTTPRequestHandler):
    def do_POST(self):
        body = b'{"error": "bad key"}'
        self.send_response(401)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def cfg_for(port, fail=False, key="k", model="mock"):
    c = Config()
    c.set("ai.provider", "openai")
    c.set("ai.base_url", f"http://127.0.0.1:{port}/v1")
    c.set("ai.model", model)
    c.set("ai.api_key", key)
    return c


def main():
    ok = HTTPServer(("127.0.0.1", 0), OK)
    bad = HTTPServer(("127.0.0.1", 0), FAIL)
    for s in (ok, bad):
        threading.Thread(target=s.serve_forever, daemon=True).start()

    # Local mode never needs an API.
    c = Config()
    c.set("ai.provider", "local")
    st = test_connection(c)
    assert st["ok"] and "no API" in st["message"], st
    print("LOCAL:", st)

    # Successful live request.
    st = test_connection(cfg_for(ok.server_port))
    assert st["ok"] and st["latency_ms"] >= 0, st
    print("OK   :", st)

    # HTTP error is reported, not raised.
    st = test_connection(cfg_for(bad.server_port))
    assert not st["ok"] and "401" in st["message"], st
    print("FAIL :", st)

    # Missing key / URL / model produce clear messages.
    c = cfg_for(ok.server_port, key="")
    st = test_connection(c)
    assert st["ok"] is True, st  # empty key is allowed for local servers like Ollama
    c = cfg_for(ok.server_port, model="")
    st = test_connection(c)
    assert not st["ok"] and "model" in st["message"].lower(), st
    c = Config()
    c.set("ai.provider", "gemini")
    st = test_connection(c)
    assert not st["ok"] and "key" in st["message"].lower(), st
    print("EDGE : all missing-config checks passed")

    ok.shutdown()
    bad.shutdown()
    print("API STATUS TEST PASSED")


if __name__ == "__main__":
    main()
