"""Durable cache for MoySklad outreach AI drafts (per client + channel).

Same backend ladder as catalog cache:

1. Redis — when ``REDIS_URL`` / ``MOYSKLAD_REDIS_URL`` is set
2. File JSON under ``$HERMES_HOME/moysklad/outreach_cache/``
3. Process-local memory

TTL default: 30 days (survive reboot). Override
``MOYSKLAD_OUTREACH_CACHE_TTL_SECONDS``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from typing import Any, Optional

from hermes_constants import get_hermes_home

log = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days
_LOCK = threading.RLock()
_MEMORY: dict[str, dict[str, Any]] = {}


def cache_ttl_seconds() -> int:
    raw = (os.environ.get("MOYSKLAD_OUTREACH_CACHE_TTL_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_TTL_SECONDS
    try:
        return max(3600, int(raw))
    except ValueError:
        return DEFAULT_TTL_SECONDS


def _redis_url() -> str:
    return (os.environ.get("REDIS_URL") or os.environ.get("MOYSKLAD_REDIS_URL") or "").strip()


def _account_fingerprint() -> str:
    token = (os.environ.get("MOYSKLAD_API_TOKEN") or "").strip()
    if not token:
        return "no-token"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def draft_cache_key(client_id: str, channel: str = "telegram") -> str:
    cid = (client_id or "").strip()
    ch = (channel or "telegram").strip().lower() or "telegram"
    return f"moysklad:outreach-draft:v1:{_account_fingerprint()}:{cid}:{ch}"


def _file_path(key: str) -> Any:
    safe = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    root = get_hermes_home() / "moysklad" / "outreach_cache"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{safe}.json"


def _redis_client():
    url = _redis_url()
    if not url:
        return None
    try:
        import redis  # type: ignore[import-not-found]
    except Exception:
        log.debug("REDIS_URL set but redis package missing; outreach file cache")
        return None
    try:
        client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2.0)
        client.ping()
        return client
    except Exception as exc:
        log.warning("MoySklad outreach Redis unavailable (%s); file cache", exc)
        return None


def _envelope(draft: dict[str, Any], *, saved_at: float) -> dict[str, Any]:
    return {
        "saved_at": float(saved_at),
        "ttl_seconds": cache_ttl_seconds(),
        "draft": draft,
    }


def _is_fresh(envelope: dict[str, Any], *, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    saved_at = float(envelope.get("saved_at") or 0)
    ttl = int(envelope.get("ttl_seconds") or cache_ttl_seconds())
    return saved_at > 0 and (now - saved_at) < ttl


def get_outreach_draft(client_id: str, channel: str = "telegram") -> Optional[dict[str, Any]]:
    """Return draft dict or None if missing/expired."""
    cid = (client_id or "").strip()
    if not cid:
        return None
    key = draft_cache_key(cid, channel)
    now = time.time()
    with _LOCK:
        mem = _MEMORY.get(key)
        if mem and _is_fresh(mem, now=now):
            draft = mem.get("draft")
            return dict(draft) if isinstance(draft, dict) else None

    client = _redis_client()
    if client is not None:
        try:
            raw = client.get(key)
            if raw:
                envelope = json.loads(raw)
                if isinstance(envelope, dict) and _is_fresh(envelope, now=now):
                    with _LOCK:
                        _MEMORY[key] = envelope
                    draft = envelope.get("draft")
                    return dict(draft) if isinstance(draft, dict) else None
        except Exception as exc:
            log.warning("MoySklad outreach Redis get failed: %s", exc)

    path = _file_path(key)
    try:
        if path.is_file():
            envelope = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(envelope, dict) and _is_fresh(envelope, now=now):
                with _LOCK:
                    _MEMORY[key] = envelope
                draft = envelope.get("draft")
                return dict(draft) if isinstance(draft, dict) else None
    except Exception as exc:
        log.warning("MoySklad outreach file cache read failed: %s", exc)
    return None


def set_outreach_draft(
    client_id: str,
    channel: str,
    draft: dict[str, Any],
    *,
    saved_at: float | None = None,
) -> dict[str, Any]:
    """Persist outreach draft; return envelope."""
    cid = (client_id or "").strip()
    ch = (channel or "telegram").strip().lower() or "telegram"
    if not cid:
        raise ValueError("client_id required")
    message = str((draft or {}).get("message") or "").strip()
    if not message:
        raise ValueError("draft.message required")

    payload = {
        "client_id": cid,
        "channel": ch,
        "message": message,
        "grounding_notes": str(draft.get("grounding_notes") or ""),
        "source": str(draft.get("source") or ""),
        "status": str(draft.get("status") or ""),
        "client_name": str(draft.get("client_name") or ""),
        "title": str(draft.get("title") or ""),
        "facts": draft.get("facts") if isinstance(draft.get("facts"), dict) else {},
        "sanity": draft.get("sanity") if isinstance(draft.get("sanity"), dict) else None,
    }
    key = draft_cache_key(cid, ch)
    envelope = _envelope(payload, saved_at=saved_at or time.time())
    ttl = int(envelope["ttl_seconds"])

    with _LOCK:
        _MEMORY[key] = envelope

    client = _redis_client()
    if client is not None:
        try:
            client.setex(key, ttl, json.dumps(envelope, ensure_ascii=False, default=str))
        except Exception as exc:
            log.warning("MoySklad outreach Redis set failed: %s", exc)

    path = _file_path(key)
    try:
        path.write_text(
            json.dumps(envelope, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception as exc:
        log.warning("MoySklad outreach file cache write failed: %s", exc)

    return envelope


def invalidate_outreach_draft(client_id: str, channel: str = "telegram") -> None:
    cid = (client_id or "").strip()
    if not cid:
        return
    key = draft_cache_key(cid, channel)
    with _LOCK:
        _MEMORY.pop(key, None)
    client = _redis_client()
    if client is not None:
        try:
            client.delete(key)
        except Exception as exc:
            log.warning("MoySklad outreach Redis delete failed: %s", exc)
    path = _file_path(key)
    try:
        if path.is_file():
            path.unlink()
    except Exception as exc:
        log.warning("MoySklad outreach file cache delete failed: %s", exc)


def cache_backend_name() -> str:
    if _redis_client() is not None:
        return "redis+file"
    return "file"


def clear_memory_for_tests() -> None:
    with _LOCK:
        _MEMORY.clear()
