"""Именованные списки клиентов — сохранённый набор фильтров Рассылок.

A segment is not a snapshot of client ids — it is the filter recipe
(``sales_filter`` / ``group`` / ``stage`` / channel knobs / …). Re-opening it
re-runs the same query against the live deduped catalog, so a client who
churns out of «Не состоялся» after a purchase drops out of the list on its
own instead of lingering as a stale id.

Persistence ladder — same idea as catalog / overlay / conversations:

1. Redis — when ``REDIS_URL`` / ``MOYSKLAD_REDIS_URL`` is set
2. File ``$HERMES_HOME/moysklad/segments.json``
3. Process-local memory
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home

log = logging.getLogger(__name__)

_LOCK = threading.RLock()
_STORE_NAME = "segments.json"
_REDIS_KEY = "moysklad:segments:v1"
_MEMORY: dict[str, Any] | None = None
_MEMORY_FP: str | None = None

#: Filter dimensions a segment remembers — same knobs Рассылки already sends.
FILTER_FIELDS = (
    "sales_filter",
    "group",
    "q",
    "group_source",
    "channel_kind",
    "require_phone",
    "require_telegram",
    "vip_only",
    "birthday_soon",
    "days_before_event",
    "event_date_from",
    "event_date_to",
    "stage",
)


def _account_fingerprint() -> str:
    token = (os.environ.get("MOYSKLAD_API_TOKEN") or "").strip()
    if not token:
        return "no-token"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _redis_url() -> str:
    return (os.environ.get("REDIS_URL") or os.environ.get("MOYSKLAD_REDIS_URL") or "").strip()


def _redis_client():
    url = _redis_url()
    if not url:
        return None
    try:
        import redis  # type: ignore[import-not-found]
    except Exception:
        return None
    try:
        client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2.0)
        client.ping()
        return client
    except Exception as exc:
        log.warning("MoySklad segments Redis unavailable (%s); file cache", exc)
        return None


def cache_backend_name() -> str:
    return "redis+file" if _redis_client() is not None else "file"


def _redis_key() -> str:
    return f"{_REDIS_KEY}:{_account_fingerprint()}"


def _store_path() -> Path:
    root = get_hermes_home() / "moysklad"
    root.mkdir(parents=True, exist_ok=True)
    return root / _STORE_NAME


def _memory_fingerprint() -> str:
    return f"{_account_fingerprint()}:{_store_path()}"


def clear_memory_for_tests() -> None:
    global _MEMORY, _MEMORY_FP
    with _LOCK:
        _MEMORY = None
        _MEMORY_FP = None


def normalize_filters(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only known filter keys, coerced to their expected type."""
    raw = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {}
    for key in FILTER_FIELDS:
        if key not in raw or raw[key] in (None, ""):
            continue
        if key in ("require_phone", "require_telegram", "vip_only", "birthday_soon"):
            out[key] = bool(raw[key])
        elif key == "days_before_event":
            try:
                out[key] = max(0, int(raw[key]))
            except (TypeError, ValueError):
                continue
        elif key in ("event_date_from", "event_date_to"):
            text = str(raw[key] or "").strip()[:10]
            if len(text) == 10 and text[4] == "-" and text[7] == "-":
                out[key] = text
        else:
            out[key] = str(raw[key]).strip()
    return out


def _load() -> dict[str, Any]:
    global _MEMORY, _MEMORY_FP
    fp = _memory_fingerprint()
    with _LOCK:
        if _MEMORY_FP == fp and isinstance(_MEMORY, dict):
            return dict(_MEMORY)

    client = _redis_client()
    if client is not None:
        try:
            raw = client.get(_redis_key())
            if raw:
                data = json.loads(raw)
                if isinstance(data, dict) and isinstance(data.get("segments"), dict):
                    with _LOCK:
                        _MEMORY = data
                        _MEMORY_FP = fp
                    return dict(data)
        except Exception as exc:
            log.warning("MoySklad segments Redis get failed: %s", exc)

    path = _store_path()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("segments"), dict):
                with _LOCK:
                    _MEMORY = data
                    _MEMORY_FP = fp
                return dict(data)
        except (OSError, json.JSONDecodeError):
            pass

    store = {"segments": {}}
    with _LOCK:
        _MEMORY = store
        _MEMORY_FP = fp
    return dict(store)


def _save(store: dict[str, Any]) -> None:
    global _MEMORY, _MEMORY_FP
    payload = {"segments": store.get("segments") or {}, "saved_at": time.time()}
    with _LOCK:
        _MEMORY = payload
        _MEMORY_FP = _memory_fingerprint()

    client = _redis_client()
    if client is not None:
        try:
            client.set(_redis_key(), json.dumps(payload, ensure_ascii=False, default=str))
        except Exception as exc:
            log.warning("MoySklad segments Redis set failed: %s", exc)

    path = _store_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def list_segments() -> list[dict[str, Any]]:
    segments = list((_load().get("segments") or {}).values())
    segments.sort(key=lambda s: str(s.get("updated_at") or ""), reverse=True)
    return segments


def get_segment(segment_id: str) -> dict[str, Any] | None:
    sid = str(segment_id or "").strip()
    if not sid:
        return None
    seg = (_load().get("segments") or {}).get(sid)
    return dict(seg) if isinstance(seg, dict) else None


def save_segment(
    *,
    segment_id: str = "",
    name: str,
    filters: dict[str, Any] | None,
    matched_total: int | None = None,
) -> dict[str, Any]:
    """Create or update a named segment. Empty ``segment_id`` creates a new one."""
    label = str(name or "").strip()
    if not label:
        raise ValueError("Название списка обязательно")
    sid = str(segment_id or "").strip() or f"seg-{uuid.uuid4().hex[:12]}"
    now = time.time()
    with _LOCK:
        store = _load()
        segments = dict(store.get("segments") or {})
        existing = segments.get(sid) if isinstance(segments.get(sid), dict) else {}
        segment = {
            "id": sid,
            "name": label,
            "filters": normalize_filters(filters),
            "matched_total": int(matched_total) if matched_total is not None else existing.get("matched_total"),
            "created_at": existing.get("created_at") or now,
            "updated_at": now,
        }
        segments[sid] = segment
        store["segments"] = segments
        _save(store)
        return dict(segment)


def delete_segment(segment_id: str) -> bool:
    sid = str(segment_id or "").strip()
    if not sid:
        return False
    with _LOCK:
        store = _load()
        segments = dict(store.get("segments") or {})
        if sid not in segments:
            return False
        del segments[sid]
        store["segments"] = segments
        _save(store)
        return True
