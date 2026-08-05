"""Draft campaign store for MoySklad CRM (Iris /campaigns).

Persists under ``HERMES_HOME/moysklad/campaigns.json``. Telegram delivery
goes through ``telegram_send`` (Business bot) from mark-sent / client card.
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


def _seller_settings_path() -> Path:
    root = get_hermes_home() / "moysklad"
    root.mkdir(parents=True, exist_ok=True)
    return root / "seller_settings.json"


def get_seller_settings() -> dict[str, str]:
    """Persisted seller identity for outreach prompts (survives restarts)."""
    empty = {
        "seller_name": "",
        "seller_facts": "",
        "telegram_business_connection_id": "",
    }
    path = _seller_settings_path()
    if not path.is_file():
        return dict(empty)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(empty)
    if not isinstance(raw, dict):
        return dict(empty)
    return {
        "seller_name": str(raw.get("seller_name") or "").strip(),
        "seller_facts": str(raw.get("seller_facts") or "").strip(),
        "telegram_business_connection_id": str(
            raw.get("telegram_business_connection_id") or ""
        ).strip(),
    }


def save_seller_settings(
    *,
    seller_name: str = "",
    seller_facts: str = "",
    telegram_business_connection_id: str | None = None,
) -> dict[str, str]:
    prev = get_seller_settings()
    biz = (
        prev.get("telegram_business_connection_id") or ""
        if telegram_business_connection_id is None
        else str(telegram_business_connection_id or "").strip()
    )
    item = {
        "seller_name": str(seller_name or "").strip(),
        "seller_facts": str(seller_facts or "").strip(),
        "telegram_business_connection_id": biz,
    }
    path = _seller_settings_path()
    tmp = path.with_suffix(".tmp")
    with _LOCK:
        tmp.write_text(
            json.dumps(item, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)
    return item


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
    audience_filters: dict[str, Any] | None = None,
    client_id: str = "",
    client_name: str = "",
    facts: dict[str, Any] | None = None,
    recommendation: str = "",
    grounding_notes: str = "",
    ai_source: str = "",
    personalize_pending: bool = False,
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
        "audience_filters": dict(audience_filters or {}),
        "personalize_pending": bool(personalize_pending),
        "client_id": (client_id or "").strip(),
        "client_name": (client_name or "").strip(),
        "facts": dict(facts or {}) if facts else {},
        "recommendation": recommendation or "",
        "grounding_notes": grounding_notes or "",
        "ai_source": ai_source or "",
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
