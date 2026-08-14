"""Durable cache for MoySklad Clients catalog.

Read path is CDN-style stale-while-revalidate:

* Fresh envelope (within TTL) → serve immediately
* Expired but still present → serve stale; caller revalidates in background
* Missing → rebuild from MoySklad (blocking)

Backends, in order:

1. Redis — when ``REDIS_URL`` is set and the ``redis`` package is importable
2. File JSON under ``$HERMES_HOME/moysklad/cache/`` (always available)
3. Process-local memory (hot layer on top of either durable store)

Logical TTL default: 6 hours (``MOYSKLAD_CACHE_TTL_SECONDS``).
Redis retention is longer than logical TTL so expired keys remain
peekable for SWR (ephemeral disks otherwise lose the file layer).
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


def redis_retention_seconds() -> int:
    """How long Redis keeps bytes after write (may exceed logical TTL).

    Keeps stale envelopes available for SWR after freshness expires.
    Override with ``MOYSKLAD_CACHE_REDIS_RETENTION_SECONDS``.
    """
    raw = (os.environ.get("MOYSKLAD_CACHE_REDIS_RETENTION_SECONDS") or "").strip()
    ttl = cache_ttl_seconds()
    default = max(ttl * 7, ttl + 7 * 24 * 60 * 60)  # ≥7× TTL or TTL+7d
    if not raw:
        return default
    try:
        return max(ttl, int(raw))
    except ValueError:
        return default


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
        "moysklad:catalog:v4",
        _account_fingerprint(),
        f"o{int(max_orders)}",
        f"c{int(max_counterparties)}",
        f"a{1 if include_archived else 0}",
    )
    return ":".join(parts)


def partial_cache_key(key: str) -> str:
    """Side-channel key for progressive rebuild flushes.

    Partial catalogs must NEVER overwrite the last complete one on the main
    key: a deploy restart mid-rebuild used to leave the durable cache
    poisoned with ``partial=True`` (UI saw 15 of 152 matches until the next
    full rebuild). Flushes land here; readers fall back to this key only
    when the main key has nothing.
    """
    return f"{key}:partial"


def refresh_audience_counts(catalog: dict[str, Any]) -> dict[str, int]:
    """Recompute exclusive tab counts + channel/sales-type fields (mutates catalog).

    Stale durable cache kept old direct/marketplace numbers and first-only
    channel strings after classifier fixes; every read path must refresh so
    UI never shows gap ``total != direct + marketplace`` or a single channel
    when the client has many.
    """
    from plugins.moysklad.dedupe import recompute_audience_counts
    from plugins.moysklad.sales_channels import refresh_row_channel_fields

    rows = list((catalog or {}).get("rows") or [])
    for row in rows:
        if isinstance(row, dict):
            refresh_row_channel_fields(row)
            # Recompute order aggregates from context when present.
            ctx = row.get("_orders_context")
            if isinstance(ctx, list) and ctx:
                amounts: list[float] = []
                last = ""
                for item in ctx:
                    if not isinstance(item, dict):
                        continue
                    try:
                        amount = float(item.get("sum") or item.get("Сумма") or 0)
                    except (TypeError, ValueError):
                        amount = 0.0
                    if amount > 0:
                        amounts.append(amount)
                    moment = str(
                        item.get("moment")
                        or item.get("Дата")
                        or item.get("date")
                        or ""
                    ).strip()
                    if moment and moment > last:
                        last = moment
                row["order_count"] = len(ctx)
                row["Всего заказов"] = len(ctx)
                if amounts:
                    avg = round(sum(amounts) / len(amounts), 2)
                    row["avg_check"] = avg
                    row["Средний чек"] = avg
                if last:
                    row["last_order_at"] = last
                    row["Дата последнего заказа"] = last
    counts = recompute_audience_counts(rows)
    if isinstance(catalog, dict):
        catalog["rows"] = rows
        catalog["counts"] = counts
    return counts


# Bump when channel / event-index / group-index / partition rules change —
# forces one full walk, then filter clicks reuse stamped catalog (cheap).
AUDIENCE_READY_VERSION = 6


def ensure_audience_ready(
    catalog: dict[str, Any],
    *,
    force: bool = False,
) -> dict[str, int]:
    """Stamp counts + event indexes once; skip O(n) channel refresh on every filter.

    Hot path: seller clicks calendar / VIP / group → ``clients_page`` used to
    call ``refresh_audience_counts`` (full channel rewrite) on ~10k rows every
    time. Scale the bottleneck: pay the walk once per catalog version, then
    only match filters.
    """
    from plugins.moysklad.audience import stamp_row_event_index
    from plugins.moysklad.dedupe import recompute_audience_counts
    from plugins.moysklad.groups import GROUP_INDEX_KEY, stamp_row_group_index

    if not isinstance(catalog, dict):
        return {"direct": 0, "marketplace": 0, "other": 0, "total": 0}

    rows = list(catalog.get("rows") or [])
    stamped = int(catalog.get("_audience_stamp_v") or 0)
    if force or stamped != AUDIENCE_READY_VERSION:
        counts = refresh_audience_counts(catalog)
        for row in rows:
            if isinstance(row, dict):
                stamp_row_event_index(row)
                stamp_row_group_index(row)
        catalog["_audience_stamp_v"] = AUDIENCE_READY_VERSION
        return counts

    # Cheap repair: rows added after stamp still get event / group indexes.
    for row in rows:
        if not isinstance(row, dict):
            continue
        if "_event_index_v1" not in row:
            stamp_row_event_index(row)
        if GROUP_INDEX_KEY not in row:
            stamp_row_group_index(row)

    counts = catalog.get("counts")
    if isinstance(counts, dict) and counts.get("total") is not None:
        return counts
    counts = recompute_audience_counts(rows)
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
    from plugins.moysklad.audience import stamp_row_event_index
    from plugins.moysklad.dedupe import dedupe_catalog_rows, merge_catalogs, recompute_audience_counts
    from plugins.moysklad.groups import GROUP_INDEX_KEY, stamp_row_group_index

    payload = dict(catalog or {})
    if merge:
        existing_env = None
        with _LOCK:
            existing_env = _MEMORY.get(key)
        if existing_env is None:
            # Peek durable store even if TTL-expired — merge needs prior rows.
            existing_env = peek_cached(key)
        prior = (existing_env or {}).get("catalog") if isinstance(existing_env, dict) else None
        payload = merge_catalogs(prior if isinstance(prior, dict) else None, payload)
    else:
        rows = dedupe_catalog_rows(payload.get("rows") or [])
        payload["rows"] = rows
        payload["counts"] = recompute_audience_counts(rows)
        payload["counterparties_deduped"] = len(rows)

    # Persist filter-ready: rows here are freshly built or already walked, so
    # repair missing/merge-invalidated indexes and mark the stamp version.
    # Otherwise partial flushes wiped `_audience_stamp_v` and every /clients
    # click during a rebuild re-ran the full O(n) audience walk in-request.
    for row in payload.get("rows") or []:
        if not isinstance(row, dict):
            continue
        if "_event_index_v1" not in row:
            stamp_row_event_index(row)
        if GROUP_INDEX_KEY not in row:
            stamp_row_group_index(row)
    payload["_audience_stamp_v"] = AUDIENCE_READY_VERSION

    envelope = _envelope(payload, synced_at=synced_at or time.time())
    redis_ttl = redis_retention_seconds()

    with _LOCK:
        _MEMORY[key] = envelope

    client = _redis_client()
    if client is not None:
        try:
            # Keep stale bytes past logical TTL so SWR can peek after expiry.
            client.setex(
                key, redis_ttl, json.dumps(envelope, ensure_ascii=False, default=str)
            )
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


def peek_cached(key: str) -> Optional[dict[str, Any]]:
    """Return envelope even when TTL-expired (for stale-while-revalidate).

    Order: process memory → Redis → file. Does not filter on freshness.
    """
    with _LOCK:
        mem = _MEMORY.get(key)
        if mem and isinstance(mem, dict) and mem.get("catalog") is not None:
            return mem

    return _peek_any(key)


def _peek_any(key: str) -> Optional[dict[str, Any]]:
    """Read durable envelope ignoring TTL (Redis / file)."""
    client = _redis_client()
    if client is not None:
        try:
            raw = client.get(key)
            if raw:
                envelope = json.loads(raw)
                if isinstance(envelope, dict):
                    with _LOCK:
                        _MEMORY[key] = envelope
                    return envelope
        except Exception as exc:
            log.warning("MoySklad Redis peek failed: %s", exc)
    path = _file_path(key)
    try:
        if path.is_file():
            envelope = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(envelope, dict):
                with _LOCK:
                    _MEMORY[key] = envelope
                return envelope
    except Exception as exc:
        log.warning("MoySklad file cache peek failed: %s", exc)
    return None


# --- Page window snapshots (instant paint + fast scroll without full catalog) -

# Keep a rolling window in Redis/file so «Ещё клиенты» does not wait on a cold
# MoySklad rebuild. First paint still requests limit=100; appends slice here.
PAGE_SNAPSHOT_ROWS = 2500
_PAGE_MEMORY: dict[str, dict[str, Any]] = {}


def page_snapshot_key(
    *,
    sales_filter: str = "all",
    group: str = "",
    q: str = "",
    group_source: str = "any",
    channel_kind: str = "",
    require_phone: bool = False,
    require_telegram: bool = False,
    vip_only: bool = False,
    birthday_soon: bool = False,
    days_before_event: int = 0,
    event_date_from: str = "",
    event_date_to: str = "",
    stage: str = "all",
    entity_type: str = "all",
) -> str:
    """Stable key for the clients page window snapshot (filter dims only)."""
    parts = (
        "moysklad:clients:page:v6",
        _account_fingerprint(),
        f"sf={(sales_filter or 'all').strip().lower()}",
        f"g={(group or '').strip().lower()}",
        f"q={(q or '').strip().lower()}",
        f"gs={(group_source or 'any').strip().lower()}",
        f"ck={(channel_kind or '').strip().lower()}",
        f"ph={1 if require_phone else 0}",
        f"tg={1 if require_telegram else 0}",
        f"vip={1 if vip_only else 0}",
        f"bd={1 if birthday_soon else 0}",
        f"dbe={int(days_before_event or 0)}",
        f"edf={(event_date_from or '').strip()}",
        f"edt={(event_date_to or '').strip()}",
        f"st={(stage or 'all').strip().lower()}",
        f"et={(entity_type or 'all').strip().lower()}",
    )
    return ":".join(parts)


def _client_row_id(row: dict[str, Any]) -> str:
    return str(
        row.get("id")
        or row.get("_moysklad_id")
        or row.get("moysklad_id")
        or ""
    ).strip()


def set_page_snapshot(
    key: str,
    page: dict[str, Any],
    *,
    synced_at: float | None = None,
) -> dict[str, Any]:
    """Persist page-window payload for instant cold/SWR paint + scroll."""
    payload = dict(page or {})
    clients = list(payload.get("clients") or [])[:PAGE_SNAPSHOT_ROWS]
    payload["clients"] = clients
    payload["returned"] = len(clients)
    envelope = {
        "synced_at": float(synced_at or time.time()),
        "page": payload,
    }
    redis_ttl = redis_retention_seconds()

    with _LOCK:
        _PAGE_MEMORY[key] = envelope

    client = _redis_client()
    if client is not None:
        try:
            client.setex(
                key, redis_ttl, json.dumps(envelope, ensure_ascii=False, default=str)
            )
        except Exception as exc:
            log.warning("MoySklad page snapshot Redis set failed: %s", exc)

    path = _file_path(key)
    try:
        path.write_text(
            json.dumps(envelope, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception as exc:
        log.warning("MoySklad page snapshot file write failed: %s", exc)

    return envelope


def extend_page_snapshot(
    key: str,
    clients: list[dict[str, Any]],
    *,
    matched_total: int | None = None,
    counts: dict[str, Any] | None = None,
    synced_at: float | None = None,
) -> dict[str, Any] | None:
    """Append newly fetched clients into the Redis/file window (dedupe by id)."""
    incoming = [c for c in (clients or []) if isinstance(c, dict)]
    if not incoming and matched_total is None and counts is None:
        return get_page_snapshot(key)

    existing_env = get_page_snapshot(key)
    prior_page = (
        existing_env.get("page") if isinstance(existing_env, dict) else None
    )
    prior_clients = list((prior_page or {}).get("clients") or [])
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in prior_clients + incoming:
        rid = _client_row_id(row)
        if not rid:
            # Keep anonymous rows at the end without collapsing.
            order.append(f"anon:{len(order)}")
            by_id[order[-1]] = row
            continue
        if rid not in by_id:
            order.append(rid)
        by_id[rid] = row
    merged = [by_id[i] for i in order][:PAGE_SNAPSHOT_ROWS]
    page = dict(prior_page or {})
    page["clients"] = merged
    page["returned"] = len(merged)
    if matched_total is not None:
        page["matched_total"] = int(matched_total)
    if counts is not None:
        page["counts"] = counts
    mt = int(page.get("matched_total") or 0)
    page["has_more"] = mt > len(merged) or bool(page.get("has_more"))
    page["next_offset"] = len(merged)
    return set_page_snapshot(
        key,
        page,
        synced_at=synced_at
        or float((existing_env or {}).get("synced_at") or time.time()),
    )


def get_page_snapshot(key: str) -> Optional[dict[str, Any]]:
    """Return page-snapshot envelope ``{synced_at, page}`` or None."""
    with _LOCK:
        mem = _PAGE_MEMORY.get(key)
        if mem and isinstance(mem, dict) and isinstance(mem.get("page"), dict):
            return mem

    client = _redis_client()
    if client is not None:
        try:
            raw = client.get(key)
            if raw:
                envelope = json.loads(raw)
                if isinstance(envelope, dict) and isinstance(envelope.get("page"), dict):
                    with _LOCK:
                        _PAGE_MEMORY[key] = envelope
                    return envelope
        except Exception as exc:
            log.warning("MoySklad page snapshot Redis get failed: %s", exc)

    path = _file_path(key)
    try:
        if path.is_file():
            envelope = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(envelope, dict) and isinstance(envelope.get("page"), dict):
                with _LOCK:
                    _PAGE_MEMORY[key] = envelope
                return envelope
    except Exception as exc:
        log.warning("MoySklad page snapshot file read failed: %s", exc)
    return None


def slice_page_snapshot(
    envelope: dict[str, Any],
    *,
    limit: int,
    offset: int = 0,
) -> Optional[dict[str, Any]]:
    """Return a response-shaped page dict sliced to ``limit``/``offset``.

    Supports offset>0 when the Redis/file window already holds those rows so
    load-more stays fast while the full catalog is still rebuilding.
    """
    page = envelope.get("page")
    if not isinstance(page, dict):
        return None
    clients = list(page.get("clients") or [])
    off = max(0, int(offset or 0))
    if off >= len(clients):
        return None
    lim = max(1, min(int(limit or PAGE_SNAPSHOT_ROWS), PAGE_SNAPSHOT_ROWS))
    sliced = clients[off : off + lim]
    if not sliced:
        return None
    out = dict(page)
    out["clients"] = sliced
    out["returned"] = len(sliced)
    matched_total = int(page.get("matched_total") or 0)
    next_off = off + len(sliced)
    out["next_offset"] = next_off
    out["has_more"] = (
        next_off < len(clients)
        or bool(page.get("has_more"))
        or matched_total > next_off
    )
    return out


def invalidate(key: str | None = None) -> None:
    with _LOCK:
        if key is None:
            _MEMORY.clear()
            _PAGE_MEMORY.clear()
        else:
            _MEMORY.pop(key, None)
            _PAGE_MEMORY.pop(key, None)

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