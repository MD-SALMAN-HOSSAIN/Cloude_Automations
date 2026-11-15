from __future__ import annotations

import random
import time
from pathlib import Path

from .util import LOG

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]

RETRIABLE = {500, 502, 503, 504}


class AuthRequired(RuntimeError):
    pass


class QuotaExceeded(RuntimeError):
    pass


def _resolve(cfg, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else cfg.root / path


def token_path(cfg) -> Path:
    return _resolve(cfg, cfg.get("youtube.token_file", "data/token.json"))


def client_secret_path(cfg) -> Path:
    return _resolve(cfg, cfg.get("youtube.client_secret_file", "data/client_secret.json"))


def _load_creds(cfg):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = token_path(cfg)
    if not path.exists():
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("stored YouTube token is unreadable: %s", exc)
        return None
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            path.write_text(creds.to_json(), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            LOG.warning("could not refresh YouTube token: %s", exc)
            return None
    return creds if creds and creds.valid else None


def authorize(cfg) -> dict:
    """One-time OAuth flow. Opens the browser and writes data/token.json."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    secret = client_secret_path(cfg)
    if not secret.exists():
        raise AuthRequired(
            f"YouTube OAuth client is missing: {secret}\n"
            "Create it once in Google Cloud Console (see README step 4) and save the JSON there."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
    LOG.info("opening your browser for Google sign-in...")
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    path = token_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json(), encoding="utf-8")
    LOG.info("saved YouTube token to %s", path)
    return channel_info(cfg)


def _service(cfg):
    from googleapiclient.discovery import build

    creds = _load_creds(cfg)
    if not creds:
        raise AuthRequired("Not signed in to YouTube. Run: shorts auth youtube")
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def channel_info(cfg) -> dict:
    service = _service(cfg)
    try:
        response = service.channels().list(part="snippet,statistics", mine=True).execute()
    except Exception as exc:  # noqa: BLE001
        raise AuthRequired(f"Could not read your YouTube channel: {exc}") from exc
    items = response.get("items") or []
    if not items:
        return {"title": "(no channel)", "id": "", "subscribers": 0, "videos": 0}
    item = items[0]
    stats = item.get("statistics", {})
    return {
        "title": item.get("snippet", {}).get("title", ""),
        "id": item.get("id", ""),
        "subscribers": int(stats.get("subscriberCount", 0) or 0),
        "videos": int(stats.get("videoCount", 0) or 0),
    }


def is_authorized(cfg) -> bool:
    return _load_creds(cfg) is not None


def _prepare_metadata(cfg, title: str, description: str, hashtags: list[str]) -> tuple[str, str, list[str]]:
    title = (title or "GTA 6 Short").strip()
    description = (description or "").strip()
    hashtags = [h for h in (hashtags or []) if h]

    missing = [h for h in hashtags if h.lower() not in description.lower()]
    if missing:
        description = (description + "\n\n" + " ".join(missing)).strip()

    # #Shorts helps discovery; keep it in the description so the title stays readable.
    if "#shorts" not in description.lower():
        description = (description + " #Shorts").strip()
    if len(description) > 4900:
        description = description[:4900].rstrip()

    if len(title) > 100:
        title = title[:97].rstrip() + "..."

    tags: list[str] = []
    for tag in hashtags + list(cfg.get("youtube.default_tags", []) or []):
        clean = str(tag).lstrip("#").strip()
        if clean and clean.lower() not in [t.lower() for t in tags]:
            tags.append(clean)
    total = 0
    capped: list[str] = []
    for tag in tags:
        if total + len(tag) + 1 > 480:
            break
        capped.append(tag)
        total += len(tag) + 1
    return title, description, capped


def upload_clip(cfg, path: Path, title: str, description: str, hashtags: list[str], publish_at: str | None = None) -> dict:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    if not Path(path).exists():
        raise FileNotFoundError(path)
    title, description, tags = _prepare_metadata(cfg, title, description, hashtags)

    privacy = str(cfg.get("youtube.privacy", "public")).lower()
    if privacy not in {"public", "unlisted", "private"}:
        privacy = "public"
    status = {
        "privacyStatus": "private" if publish_at else privacy,
        "selfDeclaredMadeForKids": bool(cfg.get("youtube.made_for_kids", False)),
        "license": "youtube",
        "embeddable": True,
    }
    if publish_at:
        status["publishAt"] = publish_at

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": str(cfg.get("youtube.category_id", "20")),
            "defaultLanguage": "en",
        },
        "status": status,
    }

    service = _service(cfg)
    media = MediaFileUpload(str(path), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    request = service.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    attempt = 0
    while response is None:
        try:
            progress, response = request.next_chunk()
            if progress:
                LOG.info("upload %d%%", int(progress.progress() * 100))
        except HttpError as exc:
            code = getattr(exc.resp, "status", None) or 0
            reason = ""
            try:
                import json as _json

                reason = _json.loads(exc.content.decode("utf-8")).get("error", {}).get("errors", [{}])[0].get("reason", "")
            except Exception:
                reason = ""
            if code == 403 and reason in {"quotaExceeded", "uploadLimitExceeded"}:
                raise QuotaExceeded(
                    "YouTube daily upload quota reached (10,000 units/day, an upload costs 1,600). "
                    "The clip stays queued for the next slot."
                ) from exc
            if code == 401:
                raise AuthRequired("YouTube sign-in expired. Run: shorts auth youtube") from exc
            attempt += 1
            if code in RETRIABLE and attempt <= 6:
                wait = min(60, 2 ** attempt + random.random())
                LOG.warning("upload error %s, retrying in %.0fs", code, wait)
                time.sleep(wait)
                continue
            raise
        except Exception as exc:  # noqa: BLE001  (network resets, SSL)
            attempt += 1
            if attempt <= 5:
                wait = min(30, 2 ** attempt)
                LOG.warning("upload interrupted (%s), retrying in %.0fs", type(exc).__name__, wait)
                time.sleep(wait)
                continue
            raise

    video_id = response.get("id", "") if response else ""
    LOG.info("uploaded: https://youtube.com/shorts/%s", video_id)
    return {
        "id": video_id,
        "url": f"https://youtube.com/shorts/{video_id}",
        "title": title,
        "privacy": status["privacyStatus"],
    }
