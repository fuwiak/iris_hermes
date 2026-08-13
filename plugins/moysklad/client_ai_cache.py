"""Durable cache for MoySklad client-card DeepSeek summaries.

Same backend ladder as outreach drafts:

1. Redis — when ``REDIS_URL`` / ``MOYSKLAD_REDIS_URL`` is set
2. File JSON under ``$HERMES_HOME/moysklad/client_ai_cache/``
3. Process-local memory

Entries are keyed by client id and stamped with a **facts fingerprint**
(orders / stats / conversation). When catalog facts change, the old
summary is treated as a miss so UI never serves stale «Саммари AI · DeepSeek».

TTL default: 30 days. Override ``MOYSKLAD_CLIENT_AI_CACHE_TTL_SECONDS``.
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
    raw = (os.environ.get("MOYSKLAD_CLIENT_AI_CACHE_TTL_SECONDS") or "").strip()
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


def ai_cache_key(client_id: str) -> str:
    cid = (client_id or "").strip()
    return f"moysklad:client-ai:v1:{_account_fingerprint()}:{cid}"


def facts_fingerprint(detail: dict[str, Any]) -> str:
    """Stable hash of the facts that must invalidate a DeepSeek summary."""
    client = detail.get("client") if isinstance(detail.get("client"), dict) else {}
    stats = detail.get("stats") if isinstance(detail.get("stats"), dict) else {}
    orders = list(detail.get("orders") or [])
    conv = detail.get("conversation") if isinstance(detail.get("conversation"), dict) else {}
    bits: list[str] = [
        str(client.get("id") or ""),
        str(stats.get("order_count") or len(orders)),
        str(stats.get("paid_order_count") or ""),
        str(stats.get("avg_check") or client.get("avg_check") or ""),
        str(stats.get("last_order") or client.get("last_order_at") or ""),
        str(client.get("tg_nick") or ""),
        str(client.get("tg_conversation") or ""),
        str(conv.get("message_count") or 0),
    ]
    for order in orders[:40]:
        if not isinstance(order, dict):
            continue
        bits.append(
            "|".join(
                [
                    str(order.get("id") or ""),
                    str(order.get("date") or "")[:19],
                    str(order.get("sum") or ""),
                    str(order.get("payment_status") or order.get("state") or ""),
                    str(order.get("channel") or "")[:40],
                ]
            )
        )
    return hashlib.sha256("\n".join(bits).encode("utf-8")).hexdigest()[:24]


def _file_path(key: str) -> Any:
    safe = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    root = get_hermes_home() / "moysklad" / "client_ai_cache"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{safe}.json"


def _redis_client():
    url = _redis_url()
    if not url:
        return None
    try:
        import redis  # type: ignore[import-not-found]
    except Exception:
        log.debug("REDIS_URL set but redis package missing; client_ai file cache")
        return None
    try:
        client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2.0)
        client.ping()
        return client
    except Exception as exc:
        log.warning("MoySklad client_ai Redis unavailable (%s); file cache", exc)
        return None


def _envelope(payload: dict[str, Any], *, saved_at: float) -> dict[str, Any]:
    return {
        "saved_at": float(saved_at),
        "ttl_seconds": cache_ttl_seconds(),
        "payload": payload,
    }


def _is_fresh(envelope: dict[str, Any], *, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    saved_at = float(envelope.get("saved_at") or 0)
    ttl = int(envelope.get("ttl_seconds") or cache_ttl_seconds())
    return saved_at > 0 and (now - saved_at) < ttl


def get_client_ai(
    client_id: str,
    *,
    fingerprint: str | None = None,
) -> Optional[dict[str, Any]]:
    """Return cached AI block or None if missing/expired/fingerprint mismatch."""
    cid = (client_id or "").strip()
    if not cid:
        return None
    key = ai_cache_key(cid)
    now = time.time()
    envelope: dict[str, Any] | None = None

    with _LOCK:
        mem = _MEMORY.get(key)
        if mem and _is_fresh(mem, now=now):
            envelope = mem

    if envelope is None:
        client = _redis_client()
        if client is not None:
            try:
                raw = client.get(key)
                if raw:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict) and _is_fresh(parsed, now=now):
                        envelope = parsed
                        with _LOCK:
                            _MEMORY[key] = parsed
            except Exception as exc:
                log.warning("MoySklad client_ai Redis get failed: %s", exc)

    if envelope is None:
        path = _file_path(key)
        try:
            if path.is_file():
                parsed = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(parsed, dict) and _is_fresh(parsed, now=now):
                    envelope = parsed
                    with _LOCK:
                        _MEMORY[key] = parsed
        except Exception as exc:
            log.warning("MoySklad client_ai file cache read failed: %s", exc)

    if not envelope:
        return None
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        return None
    if fingerprint is not None:
        cached_fp = str(payload.get("facts_fingerprint") or "")
        if cached_fp and cached_fp != fingerprint:
            return None
    ai = payload.get("ai")
    return dict(ai) if isinstance(ai, dict) else None


def set_client_ai(
    client_id: str,
    ai: dict[str, Any],
    *,
    fingerprint: str,
    saved_at: float | None = None,
) -> dict[str, Any]:
    """Persist DeepSeek (or other) card AI for ``client_id``."""
    cid = (client_id or "").strip()
    if not cid:
        raise ValueError("client_id required")
    if not isinstance(ai, dict) or not any(
        str(ai.get(k) or "").strip()
        for k in ("history_profile", "occasion_intent", "recommendation")
    ):
        raise ValueError("ai summary required")

    payload = {
        "client_id": cid,
        "facts_fingerprint": str(fingerprint or ""),
        "ai": {
            "history_profile": str(ai.get("history_profile") or "").strip(),
            "occasion_intent": str(ai.get("occasion_intent") or "").strip(),
            "recommendation": str(ai.get("recommendation") or "").strip(),
            "source": str(ai.get("source") or "llm"),
            "provider": str(ai.get("provider") or ""),
            "model": str(ai.get("model") or ""),
            "data_thin": bool(ai.get("data_thin")),
        },
    }
    key = ai_cache_key(cid)
    envelope = _envelope(payload, saved_at=saved_at or time.time())
    ttl = int(envelope["ttl_seconds"])

    with _LOCK:
        _MEMORY[key] = envelope

    client = _redis_client()
    if client is not None:
        try:
            client.setex(key, ttl, json.dumps(envelope, ensure_ascii=False, default=str))
        except Exception as exc:
            log.warning("MoySklad client_ai Redis set failed: %s", exc)

    path = _file_path(key)
    try:
        path.write_text(
            json.dumps(envelope, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception as exc:
        log.warning("MoySklad client_ai file cache write failed: %s", exc)

    return envelope


def invalidate_client_ai(client_id: str = "") -> dict[str, Any]:
    """Drop one client AI entry, or all when ``client_id`` empty."""
    cid = (client_id or "").strip()
    if cid:
        key = ai_cache_key(cid)
        with _LOCK:
            _MEMORY.pop(key, None)
        client = _redis_client()
        if client is not None:
            try:
                client.delete(key)
            except Exception as exc:
                log.warning("MoySklad client_ai Redis delete failed: %s", exc)
        path = _file_path(key)
        try:
            if path.is_file():
                path.unlink()
        except Exception as exc:
            log.warning("MoySklad client_ai file cache delete failed: %s", exc)
        return {"ok": True, "cleared": cid}

    with _LOCK:
        _MEMORY.clear()
    cleared = 0
    client = _redis_client()
    if client is not None:
        try:
            pattern = f"moysklad:client-ai:v1:{_account_fingerprint()}:*"
            for key in client.scan_iter(match=pattern, count=200):
                client.delete(key)
                cleared += 1
        except Exception as exc:
            log.warning("MoySklad client_ai Redis clear-all failed: %s", exc)
    cache_root = get_hermes_home() / "moysklad" / "client_ai_cache"
    if cache_root.is_dir():
        for path in cache_root.glob("*.json"):
            try:
                path.unlink()
                cleared += 1
            except Exception:
                pass
    return {"ok": True, "cleared": "all", "removed": cleared}


def list_cached_client_ids() -> list[str]:
    """Client ids that currently have a cached DeepSeek summary (file layer)."""
    out: list[str] = []
    cache_root = get_hermes_home() / "moysklad" / "client_ai_cache"
    if not cache_root.is_dir():
        return out
    for path in cache_root.glob("*.json"):
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            payload = envelope.get("payload") if isinstance(envelope, dict) else None
            if not isinstance(payload, dict):
                continue
            cid = str(payload.get("client_id") or "").strip()
            if cid:
                out.append(cid)
        except Exception:
            continue
    return out


def cache_backend_name() -> str:
    if _redis_client() is not None:
        return "redis+file"
    return "file"


def clear_memory_for_tests() -> None:
    with _LOCK:
        _MEMORY.clear()
