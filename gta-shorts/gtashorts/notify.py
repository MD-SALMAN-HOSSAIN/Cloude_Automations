from __future__ import annotations

import requests

from .util import LOG


def notify(cfg, message: str) -> None:
    """Best-effort Discord webhook notification. Never raises."""
    url = (cfg.get("notify.discord_webhook") or "").strip()
    if not url:
        return
    try:
        requests.post(url, json={"content": message[:1900]}, timeout=10)
    except Exception as exc:  # noqa: BLE001
        LOG.debug("notification failed: %s", exc)
