"""Mock-server test for the Upload-Post publishing integration."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.config import Config
from core.ffmpeg_tools import make_test_video
from core.upload_post import (check_connection, connected_accounts, create_profile,
                              generate_connect_url, get_status, publish_clips, upload_clip)
from core.util import Logger

RECEIVED = []


class Handler(BaseHTTPRequestHandler):
    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path.endswith("/uploadposts/me"):
            self._send({"success": True, "email": "creator@example.com", "plan": "Pro"})
        elif path.endswith("/uploadposts/users"):
            self._send({"success": True, "limit": 3, "profiles": [{
                "username": "creator",
                "display_name": "My Channel",
                "social_accounts": {
                    "tiktok": {"handle": "mychannel"},
                    "instagram": {"handle": "mychannel"},
                    "youtube": None,
                },
            }]})
        elif "/uploadposts/users/" in path:
            self._send({"success": True, "profile": {
                "username": "creator", "display_name": "My Channel",
                "social_accounts": {
                    "tiktok": {"handle": "mychannel", "display_name": "My Channel"},
                    "instagram": {"handle": "mygrid", "display_name": "My Grid"},
                    "youtube": None,
                },
            }})
        elif path.endswith("/uploadposts/status"):
            self._send({"status": "completed", "completed": 2, "total": 2,
                        "results": [{"platform": "tiktok", "success": True,
                                     "url": "https://tiktok.com/@x/video/1"}]})
        else:
            self._send({"success": False, "error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8", "replace")
        path = urlparse(self.path).path
        RECEIVED.append({
            "path": path,
            "auth": self.headers.get("Authorization", ""),
            "idem": self.headers.get("Idempotency-Key", ""),
            "body": body,
        })
        if path.endswith("/generate-jwt"):
            self._send({"success": True,
                        "access_url": "https://app.upload-post.com/connect?token=abc123",
                        "duration": "48h"})
        elif path.endswith("/uploadposts/users"):
            payload = json.loads(body or "{}")
            self._send({"success": True, "profile": {
                "username": payload.get("username", "creator"),
                "display_name": payload.get("display_name", "Viral Clip Make"),
                "social_accounts": {},
            }}, 201)
        elif path.endswith("/upload"):
            self._send({"success": True, "request_id": "req_123",
                        "message": "Upload initiated successfully in background.",
                        "total_platforms": 2})
        else:
            self._send({"success": False, "error": "not found"}, 404)

    def log_message(self, *a):
        pass


def main():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}/api"

    cfg = Config()
    cfg.set("upload.base_url", base)
    cfg.set("upload.api_key", "test-key")
    cfg.set("upload.user", "creator")
    cfg.set("upload.platforms", ["tiktok", "instagram"])
    cfg.set("upload.wait_for_status", False)
    cfg.set("upload.caption_suffix", "#shorts #viral")

    # Connection check + profile discovery
    status = check_connection(cfg)
    assert status["ok"] and status["profiles"][0]["username"] == "creator", status
    assert status["profiles"][0]["platforms"] == ["tiktok", "instagram"], status
    print("CHECK:", status["message"], "|", status["profiles"])

    # One-click connect: profile + branded link + connected account handles
    profile = create_profile(cfg, "creator", "Viral Clip Make")
    assert profile["username"] == "creator", profile
    link = generate_connect_url(cfg, "creator", ["tiktok", "instagram"])
    assert "connect?token=" in link["url"] and link["duration"] == "48h", link
    jwt_body = json.loads(RECEIVED[-1]["body"])
    assert jwt_body["connect_title"].startswith("Viral Clip Make"), jwt_body
    accts = connected_accounts(cfg, "creator")
    assert [a["platform"] for a in accts] == ["tiktok", "instagram"], accts
    assert accts[0]["handle"] == "mychannel", accts
    print("CONNECT: profile created, link generated, handles:",
          [(a["platform"], a["handle"]) for a in accts])

    # Real multipart upload of a real mp4
    work = Path(tempfile.mkdtemp(prefix="vcm_upload_"))
    video = make_test_video(work / "clip.mp4", seconds=4)
    sent = upload_clip(video, cfg, "Insane secret truth")
    assert sent["request_id"] == "req_123", sent
    req = RECEIVED[-1]
    assert req["auth"] == "Apikey test-key", req["auth"]
    assert req["path"].endswith("/upload"), req["path"]
    assert req["body"].count("platform[]") == 2, req["body"][:500]
    assert "user" in req["body"] and "creator" in req["body"], req["body"][:500]
    assert "#shorts" in req["body"], req["body"][:500]
    assert req["idem"], "no idempotency key"
    print("UPLOAD: auth ok, 2 platforms, caption + key present")

    # Preview caption override
    upload_clip(video, cfg, "Ignored title", caption="MY CUSTOM CAPTION")
    assert "MY CUSTOM CAPTION" in RECEIVED[-1]["body"], RECEIVED[-1]["body"][:500]
    print("PREVIEW CAPTION: custom caption sent unchanged")

    # Status endpoint
    st = get_status("req_123", cfg)
    assert st["status"] == "completed", st
    print("STATUS:", st["status"])

    # Batch publishing
    log = Logger(lambda level, msg: print(f"[{level}] {msg}"))
    results = publish_clips([video, video], cfg, log, titles=["One", "Two"])
    assert len(results) == 2 and all(r.get("request_id") for r in results), results
    print("BATCH:", len(results), "uploads started")

    server.shutdown()
    print("UPLOAD-POST TEST PASSED")


if __name__ == "__main__":
    main()
