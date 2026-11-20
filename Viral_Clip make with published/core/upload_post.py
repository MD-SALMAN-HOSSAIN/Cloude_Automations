"""Optional publishing through Upload-Post (https://upload-post.com).

One API key publishes finished clips to TikTok / Instagram / YouTube / etc.
The main tool stays free and local - this module is only used if the user
explicitly enables publishing and supplies their own Upload-Post key.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Callable

from .util import Logger

API_BASE = "https://api.upload-post.com/api"

PLATFORMS = {
    "tiktok": "TikTok",
    "instagram": "Instagram",
    "youtube": "YouTube",
    "facebook": "Facebook",
    "linkedin": "LinkedIn",
    "x": "X (Twitter)",
    "threads": "Threads",
    "pinterest": "Pinterest",
    "bluesky": "Bluesky",
}


def _session(cfg):
    import requests

    session = requests.Session()
    key = str(cfg.get("upload.api_key", "") or "").strip()
    if not key:
        raise RuntimeError("No Upload-Post API key. Paste it in the Publish tab.")
    session.headers.update({"Authorization": f"Apikey {key}"})
    return session


def _base(cfg) -> str:
    return str(cfg.get("upload.base_url", "") or API_BASE).strip().rstrip("/")


def _err(exc: Exception) -> str:
    import requests

    if isinstance(exc, requests.HTTPError):
        try:
            body = exc.response.json()
            for key in ("message", "error", "detail"):
                if body.get(key):
                    return f"HTTP {exc.response.status_code}: {str(body[key])[:200]}"
        except Exception:
            pass
        return f"HTTP {exc.response.status_code}"
    return " ".join(str(exc).split())[:250]


def extract_links(value, depth: int = 0) -> list[str]:
    """Find public post URLs anywhere in an API response."""
    found: list[str] = []
    if depth > 6:
        return found
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key).lower()
            if (isinstance(item, str) and item.startswith(("http://", "https://"))
                    and any(token in name for token in ("url", "link", "post", "share"))):
                found.append(item.strip())
            else:
                found.extend(extract_links(item, depth + 1))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.extend(extract_links(item, depth + 1))
    elif isinstance(value, str) and value.startswith(("http://", "https://")):
        found.append(value.strip())
    return list(dict.fromkeys(found))


def _account_info(value) -> dict:
    """Normalise one social_accounts entry (object, string, null or '')."""
    if isinstance(value, dict):
        handle = (value.get("handle") or value.get("username")
                  or value.get("display_name") or "")
        return {
            "handle": str(handle or ""),
            "display_name": str(value.get("display_name") or handle or ""),
            "avatar": str(value.get("social_images") or ""),
            "reauth_required": bool(value.get("reauth_required", False)),
            "connected": True,
        }
    if isinstance(value, str) and value.strip():
        return {"handle": value.strip(), "display_name": value.strip(),
                "avatar": "", "reauth_required": False, "connected": True}
    return {"handle": "", "display_name": "", "avatar": "",
            "reauth_required": False, "connected": False}


def _connected_platforms(accounts: dict) -> dict:
    return {p: _account_info(value) for p, value in (accounts or {}).items()
            if _account_info(value)["connected"]}


def list_profiles(cfg) -> list[dict]:
    """Return all profiles with their connected accounts and handles."""
    response = _session(cfg).get(_base(cfg) + "/uploadposts/users", timeout=30)
    response.raise_for_status()
    result = []
    for item in (response.json() or {}).get("profiles") or []:
        accounts = _connected_platforms(item.get("social_accounts") or {})
        result.append({
            "username": str(item.get("username", "")),
            "display_name": str(item.get("display_name") or item.get("username", "")),
            "platforms": list(accounts),
            "accounts": accounts,
        })
    return result


def check_connection(cfg) -> dict:
    """Validate the key and list profiles with their connected accounts."""
    try:
        session = _session(cfg)
        me = session.get(_base(cfg) + "/uploadposts/me", timeout=30)
        me.raise_for_status()

        profiles = list_profiles(cfg)
        account = {}
        try:
            account = me.json() or {}
        except Exception:
            pass
        plan = account.get("plan") or account.get("subscription") or ""
        email = account.get("email") or ""

        detail = f"Connected{(' · ' + str(plan)) if plan else ''}"
        if email:
            detail += f" · {email}"
        if not profiles:
            detail += " · no profiles yet (one will be created when you connect)"
        return {"ok": True, "message": detail, "profiles": profiles}
    except Exception as exc:
        return {"ok": False, "message": _err(exc), "profiles": []}


def create_profile(cfg, username: str, display_name: str = "Viral Clip Make") -> dict:
    """Create the app's private profile. 409 means it already exists - fine."""
    response = _session(cfg).post(
        _base(cfg) + "/uploadposts/users",
        json={"username": username, "display_name": display_name}, timeout=30,
    )
    if response.status_code == 409:
        return {"username": username, "display_name": display_name, "already_exists": True}
    response.raise_for_status()
    payload = response.json() or {}
    profile = payload.get("profile") or {}
    return {
        "username": str(profile.get("username") or username),
        "display_name": str(profile.get("display_name") or display_name),
        "already_exists": False,
    }


def generate_connect_url(cfg, username: str, platforms: list[str] | None = None) -> dict:
    """Mint the branded 48-hour hosted connect page for this profile."""
    body = {
        "username": username,
        "connect_title": "Viral Clip Make — connect your socials",
        "connect_description": (
            "Sign in to TikTok, Instagram or YouTube here. Viral Clip Make will then "
            "be able to publish your finished clips straight to your accounts."
        ),
        "redirect_button_text": "Back to Viral Clip Make",
        "show_calendar": False,
    }
    if platforms:
        body["platforms"] = list(platforms)
    response = _session(cfg).post(
        _base(cfg) + "/uploadposts/users/generate-jwt", json=body, timeout=30,
    )
    response.raise_for_status()
    payload = response.json() or {}
    url = str(payload.get("access_url") or "")
    if not url:
        raise RuntimeError("Upload-Post did not return a connect link.")
    return {"url": url, "duration": str(payload.get("duration") or "48h")}


def get_profile(cfg, username: str) -> dict:
    """Fetch one profile, including the connected account handles."""
    response = _session(cfg).get(
        _base(cfg) + "/uploadposts/users/" + username, timeout=30,
    )
    response.raise_for_status()
    payload = response.json() or {}
    profile = payload.get("profile") or {}
    accounts = _connected_platforms(profile.get("social_accounts") or {})
    return {
        "username": str(profile.get("username") or username),
        "display_name": str(profile.get("display_name") or username),
        "platforms": list(accounts),
        "accounts": accounts,
    }


def connected_accounts(cfg, username: str) -> list[dict]:
    """List connected accounts as {platform, handle, display_name, avatar}."""
    profile = get_profile(cfg, username)
    return [dict(platform=p, **info) for p, info in profile["accounts"].items()]


def disconnect_social(cfg, username: str, platform: str) -> dict:
    response = _session(cfg).request(
        "DELETE", _base(cfg) + "/uploadposts/users/social",
        json={"profile_username": username, "social_platform": platform}, timeout=30,
    )
    response.raise_for_status()
    return response.json() or {}


def upload_clip(path: Path, cfg, title: str, description: str = "",
                caption: str | None = None) -> dict:
    """Upload one video. Uses async mode so the API returns immediately."""
    path = Path(path)
    if not path.exists():
        raise RuntimeError(f"Clip not found: {path}")

    user = str(cfg.get("upload.user", "") or "").strip()
    if not user:
        raise RuntimeError("No Upload-Post profile selected. Choose one in the Publish tab.")

    platforms = cfg.get("upload.platforms", []) or []
    if isinstance(platforms, str):
        platforms = [p.strip() for p in platforms.split(",") if p.strip()]
    if not platforms:
        raise RuntimeError("Select at least one platform (TikTok, Instagram, YouTube...).")

    suffix = str(cfg.get("upload.caption_suffix", "") or "").strip()
    caption = (caption if caption is not None else
               f"{title.strip()} {suffix}".strip() if suffix else title.strip())[:2200]

    # YouTube/Reddit require a title; sending it for all platforms is safe.
    data = [
        ("user", user),
        ("title", caption),
        ("description", description or caption),
        ("async_upload", "true"),
    ]
    for platform in platforms:
        data.append(("platform[]", platform.lower()))

    headers = {"Idempotency-Key": uuid.uuid4().hex}
    session = _session(cfg)
    with path.open("rb") as fh:
        response = session.post(
            _base(cfg) + "/upload",
            headers=headers,
            data=data,
            files={"video": (path.name, fh, "video/mp4")},
            timeout=300,
        )
    response.raise_for_status()
    payload = response.json() or {}
    if payload.get("success") is False:
        raise RuntimeError(str(payload.get("message") or payload.get("error") or "Upload rejected"))
    return {
        "request_id": payload.get("request_id") or "",
        "message": payload.get("message") or "Upload started",
        "platforms": list(platforms),
        "caption": caption,
        "links": extract_links(payload),
    }


def get_status(request_id: str, cfg) -> dict:
    session = _session(cfg)
    response = session.get(
        _base(cfg) + "/uploadposts/status",
        params={"request_id": request_id}, timeout=30,
    )
    response.raise_for_status()
    return response.json() or {}


def poll_status(request_id: str, cfg, timeout: int = 900,
                on_update: Callable[[str, dict], None] | None = None) -> dict:
    """Poll until finished (or timeout). Never raises for transient errors."""
    started = time.time()
    last = {}
    while time.time() - started < timeout:
        try:
            last = get_status(request_id, cfg)
            status = str(last.get("status", "")).lower()
            if on_update:
                on_update(status, last)
            if status in ("completed", "failed", "not_found"):
                return last
        except Exception as exc:
            if on_update:
                on_update("error", {"message": _err(exc)})
        time.sleep(8)
    return last or {"status": "timeout", "message": "Status check timed out"}


def publish_clips(clips: list[Path], cfg, log: Logger,
                  titles: list[str] | None = None,
                  captions: list[str] | None = None,
                  on_progress: Callable[[float, str], None] | None = None) -> list[dict]:
    """Upload several clips, optionally waiting for each to publish."""
    titles = titles or [p.stem for p in clips]
    captions = captions or [None] * len(clips)
    wait = bool(cfg.get("upload.wait_for_status", True))
    results = []
    total = max(1, len(clips))
    for index, (path, title, caption) in enumerate(zip(clips, titles, captions), start=1):
        try:
            if on_progress:
                on_progress((index - 1) / total, f"Uploading clip {index}/{total}")
            log.info("Publishing clip %d/%d: %s", index, total, path.name)
            sent = upload_clip(path, cfg, title, caption=caption)
            log.ok("Upload started: %s (%s)", path.name, ", ".join(sent["platforms"]))
            if wait and sent["request_id"]:
                final = poll_status(
                    sent["request_id"], cfg,
                    on_update=lambda s, d: log.info("Upload status: %s (%s/%s)", s,
                                                    d.get("completed", "?"), d.get("total", "?"))
                    if s not in ("processing", "queued", "pending", "in_progress") else None,
                )
                sent["status"] = final.get("status", "")
                sent["results"] = final.get("results") or []
                links = extract_links(final)
                sent["links"] = list(dict.fromkeys(sent.get("links", []) + links))
                log.ok("Publish result: %s", sent["status"])
                if sent["links"]:
                    log.ok("Public post link(s): %s", " | ".join(sent["links"][:4]))
                elif str(sent["status"]).lower() in ("completed", "success", "published"):
                    log.warn("Upload completed but no public link was returned. Open the "
                             "platform app to confirm the post is public (not private/review).")
                if str(sent["status"]).lower() in ("failed", "rejected", "error"):
                    log.warn("Platform rejected this upload: %s",
                             str(final.get("message") or final.get("error") or sent["status"])[:200])
            results.append(sent)
        except Exception as exc:
            msg = _err(exc)
            log.warn("Could not publish %s: %s", path.name, msg)
            results.append({"error": msg, "path": str(path)})
        if on_progress:
            on_progress(index / total, f"Published clip {index}/{total}")
    return results
