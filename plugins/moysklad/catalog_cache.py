"""Durable cache for MoySklad Clients catalog.

Read path serves cached catalog until TTL expires or an explicit sync
(force=True) refreshes from MoySklad. Backends, in order:

1. Redis — when ``REDIS_URL`` is set and the ``redis`` package is importable
2. File JSON under ``$HERMES_HOME/moysklad/cache/`` (always available)
3. Process-local memory (hot layer on top of either durable store)

TTL default: 6 hours. Override with ``MOYSKLAD_CACHE_TTL_SECONDS``.
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

DEFAULT_TTL_SECONDS = 6 * 60 * 60  # 6 hours
_LOCK = threading.RLock()
_MEMORY: dict[str, dict[str, Any]] = {}


def cache_ttl_seconds() -> int:
    raw = (os.environ.get("MOYSKLAD_CACHE_TTL_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_TTL_SECONDS
    try:
        return max(60, int(raw))
    except ValueError:
        return DEFAULT_TTL_SECONDS


def _redis_url() -> str:
    return (os.environ.get("REDIS_URL") or os.environ.get("MOYSKLAD_REDIS_URL") or "").strip()


def _account_fingerprint() -> str:
    """Stable org key from token hash (never store the raw token)."""
    token = (os.environ.get("MOYSKLAD_API_TOKEN") or "").strip()
    if not token:
        return "no-token"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def cache_key(
    *,
    max_orders: int,
    max_counterparties: int,
    include_archived: bool,
) -> str:
    parts = (
        "moysklad:catalog:v2",
        _account_fingerprint(),
        f"o{int(max_orders)}",
        f"c{int(max_counterparties)}",
        f"a{1 if include_archived else 0}",
    )
    return ":".join(parts)


def refresh_audience_counts(catalog: dict[str, Any]) -> dict[str, int]:
    """Recompute exclusive tab counts on catalog rows (mutates ``catalog``).

    Stale durable cache kept old direct/marketplace numbers after classifier
    fixes; every read path must refresh so UI never shows gap
    ``total != direct + marketplace``.
    """
    from plugins.moysklad.dedupe import recompute_audience_counts

    rows = list((catalog or {}).get("rows") or [])
    counts = recompute_audience_counts(rows)
    if isinstance(catalog, dict):
        catalog["counts"] = counts
    return counts


def _file_path(key: str) -> Any:
    safe = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    root = get_hermes_home() / "moysklad" / "cache"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{safe}.json"


def _redis_client():
    url = _redis_url()
    if not url:
        return None
    try:
        import redis  # type: ignore[import-not-found]
    except Exception:
        log.debug("REDIS_URL set but redis package missing; using file cache")
        return None
    try:
        client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2.0)
        client.ping()
        return client
    except Exception as exc:
        log.warning("MoySklad Redis unavailable (%s); using file cache", exc)
        return None


def _envelope(catalog: dict[str, Any], *, synced_at: float) -> dict[str, Any]:
    return {
        "synced_at": float(synced_at),
        "ttl_seconds": cache_ttl_seconds(),
        "catalog": catalog,
    }


def _is_fresh(envelope: dict[str, Any], *, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    synced_at = float(envelope.get("synced_at") or 0)
    ttl = int(envelope.get("ttl_seconds") or cache_ttl_seconds())
    return synced_at > 0 and (now - synced_at) < ttl


def get_cached(key: str) -> Optional[dict[str, Any]]:
    """Return fresh envelope ``{synced_at, ttl_seconds, catalog}`` or None."""
    now = time.time()
    with _LOCK:
        mem = _MEMORY.get(key)
        if mem and _is_fresh(mem, now=now):
            return mem

    # Redis
    client = _redis_client()
    if client is not None:
        try:
            raw = client.get(key)
            if raw:
                envelope = json.loads(raw)
                if isinstance(envelope, dict) and _is_fresh(envelope, now=now):
                    with _LOCK:
                        _MEMORY[key] = envelope
                    return envelope
        except Exception as exc:
            log.warning("MoySklad Redis get failed: %s", exc)

    # File
    path = _file_path(key)
    try:
        if path.is_file():
            envelope = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(envelope, dict) and _is_fresh(envelope, now=now):
                with _LOCK:
                    _MEMORY[key] = envelope
                return envelope
    except Exception as exc:
        log.warning("MoySklad file cache read failed: %s", exc)
    return None


def set_cached(
    key: str,
    catalog: dict[str, Any],
    *,
    synced_at: float | None = None,
    merge: bool = False,
) -> dict[str, Any]:
    """Persist catalog and return the stored envelope.

    When ``merge=True``, stage-4 update-in-place merges into any existing
    cached catalog (by canonical id / contact keys) instead of clobbering.
    Full sync (default) replaces, but still runs dedupe on the payload.
    """
    from plugins.moysklad.dedupe import dedupe_catalog_rows, merge_catalogs, recompute_audience_counts

    payload = dict(catalog or {})
    if merge:
        existing_env = None
        with _LOCK:
            existing_env = _MEMORY.get(key)
        if existing_env is None:
            # Peek durable store even if TTL-expired — merge needs prior rows.
            existing_env = _peek_any(key)
        prior = (existing_env or {}).get("catalog") if isinstance(existing_env, dict) else None
        payload = merge_catalogs(prior if isinstance(prior, dict) else None, payload)
    else:
        rows = dedupe_catalog_rows(payload.get("rows") or [])
        payload["rows"] = rows
        payload["counts"] = recompute_audience_counts(rows)
        payload["counterparties_deduped"] = len(rows)

    envelope = _envelope(payload, synced_at=synced_at or time.time())
    ttl = int(envelope["ttl_seconds"])

    with _LOCK:
        _MEMORY[key] = envelope

    client = _redis_client()
    if client is not None:
        try:
            client.setex(key, ttl, json.dumps(envelope, ensure_ascii=False, default=str))
        except Exception as exc:
            log.warning("MoySklad Redis set failed: %s", exc)

    path = _file_path(key)
    try:
        path.write_text(
            json.dumps(envelope, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception as exc:
        log.warning("MoySklad file cache write failed: %s", exc)

    return envelope


def _peek_any(key: str) -> Optional[dict[str, Any]]:
    """Read durable envelope ignoring TTL (for stage-4 merges)."""
    client = _redis_client()
    if client is not None:
        try:
            raw = client.get(key)
            if raw:
                envelope = json.loads(raw)
                if isinstance(envelope, dict):
                    return envelope
        except Exception as exc:
            log.warning("MoySklad Redis peek failed: %s", exc)
    path = _file_path(key)
    try:
        if path.is_file():
            envelope = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(envelope, dict):
                return envelope
    except Exception as exc:
        log.warning("MoySklad file cache peek failed: %s", exc)
    return None


def invalidate(key: str | None = None) -> None:
    with _LOCK:
        if key is None:
            _MEMORY.clear()
        else:
            _MEMORY.pop(key, None)

    if key is None:
        return

    client = _redis_client()
    if client is not None:
        try:
            client.delete(key)
        except Exception as exc:
            log.warning("MoySklad Redis delete failed: %s", exc)

    path = _file_path(key)
    try:
        if path.is_file():
            path.unlink()
    except Exception as exc:
        log.warning("MoySklad file cache delete failed: %s", exc)


def cache_backend_name() -> str:
    if _redis_client() is not None:
        return "redis+file"
    return "file"


def format_synced_at(synced_at: float | None) -> str:
    if not synced_at:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(synced_at)))
    except (TypeError, ValueError, OSError):
        return ""
