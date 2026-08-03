"""Draft campaign store for MoySklad CRM (Iris /campaigns).

Persists under ``HERMES_HOME/moysklad/campaigns.json``. Drafts only —
send is left to messaging platforms / agent tools.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home

_LOCK = threading.Lock()


def _store_path() -> Path:
    root = get_hermes_home() / "moysklad"
    root.mkdir(parents=True, exist_ok=True)
    return root / "campaigns.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> list[dict[str, Any]]:
    path = _store_path()
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return raw if isinstance(raw, list) else []


def _save(items: list[dict[str, Any]]) -> None:
    path = _store_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(items, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def list_campaigns() -> list[dict[str, Any]]:
    with _LOCK:
        return list(_load())


def create_draft(
    *,
    title: str,
    channel: str = "telegram",
    mode: str = "manual",
    offer: str = "",
    sales_filter: str = "all",
    group: str = "",
    q: str = "",
    audience_count: int = 0,
    audience_preview: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    title = (title or "").strip() or "Рассылка"
    channel = (channel or "telegram").strip().lower()
    mode = "auto" if str(mode).lower() == "auto" else "manual"
    item = {
        "id": str(uuid.uuid4()),
        "title": title,
        "channel": channel,
        "mode": mode,
        "offer": offer or "",
        "status": "draft",
        "sales_filter": sales_filter or "all",
        "group": group or "",
        "q": q or "",
        "audience_count": int(audience_count or 0),
        "audience_preview": list(audience_preview or [])[:20],
        "created_at": _now(),
        "updated_at": _now(),
    }
    with _LOCK:
        items = _load()
        items.insert(0, item)
        _save(items)
    return item


def delete_campaign(campaign_id: str) -> bool:
    cid = str(campaign_id or "").strip()
    if not cid:
        return False
    with _LOCK:
        items = _load()
        next_items = [c for c in items if str(c.get("id")) != cid]
        if len(next_items) == len(items):
            return False
        _save(next_items)
    return True
