"""MoySklad Clients dashboard plugin — backend API.

Mounted at /api/plugins/moysklad/ by the dashboard plugin system.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Iterator

try:
    from fastapi import APIRouter, HTTPException, Query
    from fastapi.responses import StreamingResponse
    from pydantic import BaseModel, Field
except Exception:  # pragma: no cover — unit tests without fastapi
    class APIRouter:  # type: ignore[no-redef]
        def get(self, *_a, **_k):
            return lambda fn: fn

        def post(self, *_a, **_k):
            return lambda fn: fn

        def put(self, *_a, **_k):
            return lambda fn: fn

        def delete(self, *_a, **_k):
            return lambda fn: fn

    class HTTPException(Exception):  # type: ignore[no-redef]
        def __init__(self, status_code: int = 500, detail: str = ""):
            self.status_code = status_code
            self.detail = detail

    def Query(default=None, **_k):  # type: ignore[misc]
        return default

    class BaseModel:  # type: ignore[no-redef]
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

        def model_dump(self) -> dict:
            return dict(self.__dict__)

    def Field(default=None, **_k):  # type: ignore[misc]
        return default

    class StreamingResponse:  # type: ignore[no-redef]
        def __init__(self, content, media_type: str = "application/json", headers=None):
            self.body_iterator = content
            self.media_type = media_type
            self.headers = headers or {}



from plugins.moysklad.assign_groups import (
    propose_groups_for_rows,
    push_merged_tags,
)
from plugins.moysklad.recalculate_groups import (
    assign_to_taxonomy,
    load_taxonomy,
    propose_taxonomy,
    save_taxonomy,
)
from plugins.moysklad.campaigns import (
    create_draft,
    delete_campaign,
    get_seller_settings,
    list_campaigns,
    save_seller_settings,
)
from plugins.moysklad.telegram_send import (
    resolve_peer_identity,
    send_outreach_to_client,
    telegram_account_snapshot,
    telegram_send_status,
)
from plugins.moysklad.outreach_contacts import (
    add_custom_contact,
    delete_custom_contact,
    get_contact,
    list_outreach_contacts,
)
from plugins.moysklad.catalog_cache import (
    PAGE_SNAPSHOT_ROWS,
    cache_backend_name,
    cache_key,
    cache_ttl_seconds,
    format_synced_at,
    get_cached,
    get_page_snapshot,
    invalidate,
    page_snapshot_key,
    peek_cached,
    refresh_audience_counts,
    set_cached,
    set_page_snapshot,
    slice_page_snapshot,
)
from plugins.moysklad.classify import (
    build_enriched_catalog,
    catalog_integrity,
    clients_page,
)
from plugins.moysklad.groups import ensure_group_options_by_source
from plugins.moysklad.client import MoySkladClient, MoySkladError, token_configured
from plugins.moysklad.ai_playground import (
    get_golden_client,
    list_golden_clients,
    run_playground,
)
from plugins.moysklad.client_card import (
    build_client_detail,
    find_row_in_catalog,
    generate_ai_for_detail,
)
from plugins.moysklad.conversations import (
    append_message,
    enrich_clients,
    get_thread,
    sync_from_gateway,
)
from plugins.moysklad.outreach import (
    build_outreach_for_row,
    facts_panel,
    generate_outreach_message,
    iter_generate_outreach_for_row_events,
    iter_paraphrase_outreach_events,
    iter_personalize_batch_events,
    iter_rewrite_outreach_events,
    iter_suggest_bouquet_events,
    normalize_seller_fields,
    paraphrase_outreach_message,
    rewrite_outreach_message,
    sanity_check_outreach_message,
    suggest_historical_bouquet_message,
)
from plugins.moysklad.outreach_cache import (
    cache_backend_name as outreach_cache_backend_name,
    get_outreach_draft,
    set_outreach_draft,
)
from plugins.moysklad.ai_fill import (
    cache_backend_name as ai_fill_cache_backend_name,
    fill_empty_for_rows,
)

log = logging.getLogger(__name__)

router = APIRouter()

_SYNC_LOCK = threading.Lock()
_REVALIDATE_LOCK = threading.Lock()
_REVALIDATE_IN_FLIGHT: set[str] = set()
_SNAPSHOT_REFRESH_LOCK = threading.Lock()
_SNAPSHOT_REFRESH_IN_FLIGHT: set[str] = set()


def _apply_telegram_export_and_recache(
    catalog: dict[str, Any],
    *,
    max_orders: int,
    max_counterparties: int,
    include_archived: bool,
    force_import: bool = False,
) -> dict[str, Any]:
    """Import/stamp Telegram export onto catalog rows and rewrite durable cache."""
    from plugins.moysklad.telegram_export import (
        cache_backend_name as tg_cache_backend_name,
        ensure_export_imported,
        import_export_into_catalog,
        stamp_catalog_rows_from_overlay,
    )

    rows = list(catalog.get("rows") or [])
    if force_import:
        result = import_export_into_catalog(rows, force=True)
    else:
        result = ensure_export_imported(rows)
    # Always stamp from cache (Redis/file) so warm restarts still fill rows.
    stamped = stamp_catalog_rows_from_overlay(rows)
    catalog["rows"] = rows
    key = cache_key(
        max_orders=max_orders,
        max_counterparties=max_counterparties,
        include_archived=include_archived,
    )
    try:
        set_cached(key, catalog, synced_at=float(time.time()))
    except Exception:
        log.warning("moysklad catalog recache after tg-export failed", exc_info=True)
    out = dict(result or {})
    out["stamped_rows"] = max(int(out.get("stamped_rows") or 0), stamped)
    out["cache_backend"] = out.get("cache_backend") or tg_cache_backend_name()
    return out


def _schedule_snapshot_refresh(
    snap_key: str,
    *,
    catalog: dict[str, Any] | None,
    sales_filter: str,
    group: str,
    q: str,
    group_source: str,
    channel_kind: str,
    require_phone: bool,
    require_telegram: bool,
    vip_only: bool,
    birthday_soon: bool,
    days_before_event: int,
    max_orders: int,
    max_counterparties: int,
    include_archived: bool,
) -> bool:
    """Rebuild first-100 snapshot + telegram export in a daemon thread."""
    if catalog is None or not isinstance(catalog, dict):
        return False
    with _SNAPSHOT_REFRESH_LOCK:
        if snap_key in _SNAPSHOT_REFRESH_IN_FLIGHT:
            return False
        _SNAPSHOT_REFRESH_IN_FLIGHT.add(snap_key)

    def _worker() -> None:
        try:
            try:
                _apply_telegram_export_and_recache(
                    catalog,
                    max_orders=max_orders,
                    max_counterparties=max_counterparties,
                    include_archived=include_archived,
                    force_import=False,
                )
            except Exception:
                log.warning("moysklad telegram export (bg) failed", exc_info=True)
            page = clients_page(
                _client(),
                sales_filter=sales_filter,
                group=group,
                q=q,
                channel_kind=channel_kind,
                require_phone=require_phone,
                require_telegram=require_telegram,
                vip_only=vip_only,
                birthday_soon=birthday_soon,
                group_source=group_source,
                days_before_event=days_before_event,
                limit=PAGE_SNAPSHOT_ROWS,
                offset=0,
                max_orders=max_orders,
                max_counterparties=max_counterparties,
                include_archived=include_archived,
                catalog=catalog,
            )
            page["clients"] = enrich_clients(list(page.get("clients") or []))
            set_page_snapshot(
                snap_key,
                _strip_internal(page),
                synced_at=float(catalog.get("synced_at") or time.time()),
            )
        except Exception:
            log.warning("moysklad snapshot bg refresh failed", exc_info=True)
        finally:
            with _SNAPSHOT_REFRESH_LOCK:
                _SNAPSHOT_REFRESH_IN_FLIGHT.discard(snap_key)

    threading.Thread(
        target=_worker,
        name=f"moysklad-snap-{snap_key[-12:]}",
        daemon=True,
    ).start()
    return True


def _catalog_meta(
    envelope: dict[str, Any],
    *,
    cached: bool,
    stale: bool = False,
    revalidating: bool = False,
    counts_refreshed: bool = False,
) -> dict[str, Any]:
    synced_at = float(envelope.get("synced_at") or 0)
    return {
        "cached": cached,
        "stale": stale,
        "revalidating": revalidating,
        "counts_refreshed": counts_refreshed,
        "synced_at": synced_at,
        "synced_at_label": format_synced_at(synced_at),
        "cache_ttl_seconds": int(envelope.get("ttl_seconds") or cache_ttl_seconds()),
        "cache_backend": cache_backend_name(),
    }


def _rebuild_catalog_locked(
    key: str,
    *,
    max_orders: int,
    max_counterparties: int,
    include_archived: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Blocking MoySklad rebuild; caller must hold ``_SYNC_LOCK`` when needed."""
    client = _client()
    catalog = build_enriched_catalog(
        client,
        max_orders=max_orders,
        max_counterparties=max_counterparties,
        include_archived=include_archived,
    )
    envelope = set_cached(key, catalog)
    return catalog, _catalog_meta(envelope, cached=False)


def _schedule_catalog_revalidate(
    key: str,
    *,
    max_orders: int,
    max_counterparties: int,
    include_archived: bool,
) -> bool:
    """Kick a single background rebuild for ``key``. Returns True if scheduled."""
    with _REVALIDATE_LOCK:
        if key in _REVALIDATE_IN_FLIGHT:
            return False
        _REVALIDATE_IN_FLIGHT.add(key)

    def _worker() -> None:
        try:
            with _SYNC_LOCK:
                # Fresh write may have landed while we were queued.
                if get_cached(key) is not None:
                    return
                _rebuild_catalog_locked(
                    key,
                    max_orders=max_orders,
                    max_counterparties=max_counterparties,
                    include_archived=include_archived,
                )
        except Exception:
            log.exception("moysklad catalog background revalidate failed key=%s", key)
        finally:
            with _REVALIDATE_LOCK:
                _REVALIDATE_IN_FLIGHT.discard(key)

    threading.Thread(
        target=_worker,
        name=f"moysklad-revalidate-{key[-12:]}",
        daemon=True,
    ).start()
    return True


def _get_catalog(
    *,
    max_orders: int = 5000,
    max_counterparties: int = 0,
    include_archived: bool = False,
    force: bool = False,
    blocking: bool = True,
    refresh_counts: bool = True,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Return ``(catalog, meta)`` where meta has sync/cache fields.

    CDN-style stale-while-revalidate:
    - Fresh durable cache → serve (no MoySklad)
    - Expired but peekable → serve stale + background refresh
    - Missing + ``blocking=False`` → ``(None, meta)`` + background refresh
    - Missing / ``force=True`` + ``blocking=True`` → blocking rebuild
    """
    key = cache_key(
        max_orders=max_orders,
        max_counterparties=max_counterparties,
        include_archived=include_archived,
    )
    if not force:
        envelope = get_cached(key)
        if envelope is not None:
            catalog = envelope["catalog"]
            synced_at = float(envelope.get("synced_at") or 0)
            counts_rewritten = False
            if refresh_counts:
                catalog, counts_rewritten = _refresh_cached_catalog_counts(
                    key, catalog, synced_at=synced_at
                )
            return catalog, _catalog_meta(
                envelope, cached=True, counts_refreshed=counts_rewritten
            )

        # Logical TTL expired — still serve durable bytes if present.
        stale_env = peek_cached(key)
        if stale_env is not None and isinstance(stale_env.get("catalog"), dict):
            catalog = stale_env["catalog"]
            # Skip O(n) counts rewrite on stale path — keep first paint fast.
            scheduled = _schedule_catalog_revalidate(
                key,
                max_orders=max_orders,
                max_counterparties=max_counterparties,
                include_archived=include_archived,
            )
            return catalog, _catalog_meta(
                stale_env,
                cached=True,
                stale=True,
                revalidating=scheduled or key in _REVALIDATE_IN_FLIGHT,
                counts_refreshed=False,
            )

        if not blocking:
            scheduled = _schedule_catalog_revalidate(
                key,
                max_orders=max_orders,
                max_counterparties=max_counterparties,
                include_archived=include_archived,
            )
            return None, {
                "cached": False,
                "stale": True,
                "revalidating": scheduled or key in _REVALIDATE_IN_FLIGHT,
                "counts_refreshed": False,
                "synced_at": 0.0,
                "synced_at_label": "",
                "cache_ttl_seconds": cache_ttl_seconds(),
                "cache_backend": cache_backend_name(),
                "snapshot": False,
            }

    with _SYNC_LOCK:
        # Another request may have filled the cache while we waited.
        if not force:
            envelope = get_cached(key)
            if envelope is not None:
                catalog = envelope["catalog"]
                synced_at = float(envelope.get("synced_at") or 0)
                counts_rewritten = False
                if refresh_counts:
                    catalog, counts_rewritten = _refresh_cached_catalog_counts(
                        key, catalog, synced_at=synced_at
                    )
                return catalog, _catalog_meta(
                    envelope, cached=True, counts_refreshed=counts_rewritten
                )
            stale_env = peek_cached(key)
            if stale_env is not None and isinstance(stale_env.get("catalog"), dict):
                catalog = stale_env["catalog"]
                scheduled = _schedule_catalog_revalidate(
                    key,
                    max_orders=max_orders,
                    max_counterparties=max_counterparties,
                    include_archived=include_archived,
                )
                return catalog, _catalog_meta(
                    stale_env,
                    cached=True,
                    stale=True,
                    revalidating=scheduled or key in _REVALIDATE_IN_FLIGHT,
                    counts_refreshed=False,
                )
            if not blocking:
                scheduled = _schedule_catalog_revalidate(
                    key,
                    max_orders=max_orders,
                    max_counterparties=max_counterparties,
                    include_archived=include_archived,
                )
                return None, {
                    "cached": False,
                    "stale": True,
                    "revalidating": scheduled or key in _REVALIDATE_IN_FLIGHT,
                    "counts_refreshed": False,
                    "synced_at": 0.0,
                    "synced_at_label": "",
                    "cache_ttl_seconds": cache_ttl_seconds(),
                    "cache_backend": cache_backend_name(),
                    "snapshot": False,
                }

        return _rebuild_catalog_locked(
            key,
            max_orders=max_orders,
            max_counterparties=max_counterparties,
            include_archived=include_archived,
        )


class AiFillBody(BaseModel):
    sales_filter: str = "all"
    group: str = ""
    q: str = ""
    ids: list[str] = Field(default_factory=list)
    limit: int = 100
    use_llm: bool = True
    force: bool = False
    max_orders: int = 5000
    max_counterparties: int = 0
    include_archived: bool = False


class AssignBody(BaseModel):
    sales_filter: str = "all"
    group: str = ""
    q: str = ""
    ids: list[str] = Field(default_factory=list)
    dry_run: bool = True
    max_orders: int = 5000
    max_counterparties: int = 0
    include_archived: bool = False


class PushBody(BaseModel):
    assignments: list[dict[str, Any]] = Field(default_factory=list)
    only_changed: bool = True


class RecalculateProposeBody(BaseModel):
    sales_filter: str = "all"
    group: str = ""
    q: str = ""
    channel_kind: str = ""
    require_phone: bool = False
    require_telegram: bool = False
    vip_only: bool = False
    birthday_soon: bool = False
    group_source: str = "any"
    days_before_event: int = 0
    max_orders: int = 5000
    max_counterparties: int = 0
    include_archived: bool = False


class RecalculateApplyBody(BaseModel):
    groups: list[str] = Field(default_factory=list)
    sales_filter: str = "all"
    group: str = ""
    q: str = ""
    channel_kind: str = ""
    require_phone: bool = False
    require_telegram: bool = False
    vip_only: bool = False
    birthday_soon: bool = False
    group_source: str = "any"
    days_before_event: int = 0
    dry_run: bool = True
    push: bool = False
    max_orders: int = 5000
    max_counterparties: int = 0
    include_archived: bool = False


class CampaignCreateBody(BaseModel):
    title: str = "Рассылка"
    channel: str = "telegram"
    mode: str = "manual"
    offer: str = ""
    sales_filter: str = "all"
    group: str = ""
    q: str = ""
    channel_kind: str = ""
    require_phone: bool = False
    require_telegram: bool = False
    vip_only: bool = False
    birthday_soon: bool = False
    group_source: str = "any"
    days_before_event: int = 0
    personalize: bool = False
    client_id: str = ""
    include_preview: bool = True
    generate_ai: bool = False
    seller_name: str = ""
    seller_facts: str = ""
    max_orders: int = 5000
    max_counterparties: int = 0
    include_archived: bool = False


class OutreachGenerateBody(BaseModel):
    client_id: str = ""
    channel: str = "telegram"
    refresh_ai: bool = True
    seller_name: str = ""
    seller_facts: str = ""
    provider: str = ""
    model: str = ""
    max_orders: int = 5000
    max_counterparties: int = 0
    include_archived: bool = False


class OutreachRewriteBody(BaseModel):
    message: str = ""
    channel: str = "telegram"
    client_id: str = ""
    seller_name: str = ""
    seller_facts: str = ""
    provider: str = ""
    model: str = ""
    max_orders: int = 5000
    max_counterparties: int = 0
    include_archived: bool = False


class OutreachSanityBody(BaseModel):
    message: str = ""
    channel: str = "telegram"
    client_id: str = ""
    seller_name: str = ""
    seller_facts: str = ""
    apply_revision: bool = True
    max_orders: int = 5000
    max_counterparties: int = 0
    include_archived: bool = False


class OutreachPersonalizeBody(BaseModel):
    """Batch personalize for current audience filters (parallel LLM)."""

    channel: str = "telegram"
    sales_filter: str = "all"
    group: str = ""
    q: str = ""
    channel_kind: str = ""
    require_phone: bool = False
    require_telegram: bool = False
    vip_only: bool = False
    birthday_soon: bool = False
    group_source: str = "any"
    days_before_event: int = 0
    seller_name: str = ""
    seller_facts: str = ""
    provider: str = ""
    model: str = ""
    limit: int = 20
    max_workers: int = 3
    max_orders: int = 5000
    max_counterparties: int = 0
    include_archived: bool = False


class ConversationAppendBody(BaseModel):
    text: str = ""
    direction: str = "outbound"
    channel: str = "telegram"
    label: str = ""
    source: str = "manual"
    open_deep_link: bool = False


class ClientAiBody(BaseModel):
    """Optional model override for client-card summary / recommendation."""

    provider: str = ""
    model: str = ""
    max_orders: int = 5000
    max_counterparties: int = 0
    include_archived: bool = False


class MarkSentBody(BaseModel):
    message: str = ""
    channel: str = "telegram"
    client_id: str = ""
    open_deep_link: bool = True
    # When true (default for telegram), attempt Bot API send via Business bot.
    deliver: bool = True


class MarkSentBatchBody(BaseModel):
    """Send one draft to many audience clients (same text, sequential deliver)."""

    message: str = ""
    channel: str = "telegram"
    client_ids: list[str] = Field(default_factory=list)
    open_deep_link: bool = False
    deliver: bool = True


class OutreachContactBody(BaseModel):
    name: str = ""
    tg_nick: str = ""
    tg_chat_id: str = ""
    # Free-form: @nick, t.me/…, or numeric id — resolved via Bot API getChat.
    query: str = ""
    resolve: bool = True


class OutreachResolveBody(BaseModel):
    query: str = ""
    tg_nick: str = ""
    tg_chat_id: str = ""


class SellerSettingsBody(BaseModel):
    seller_name: str = ""
    seller_facts: str = ""
    # None = leave unchanged; "" = clear stored business connection id.
    telegram_business_connection_id: str | None = None


class PlaygroundRunBody(BaseModel):
    """AI playground: golden client id and/or editable facts JSON."""

    client_id: str = ""
    input_json: str = ""
    run_llm: bool = False


class OutreachDraftCacheBody(BaseModel):
    client_id: str = ""
    channel: str = "telegram"
    message: str = ""
    grounding_notes: str = ""
    source: str = ""
    status: str = ""
    client_name: str = ""
    title: str = ""
    facts: dict[str, Any] = Field(default_factory=dict)
    sanity: dict[str, Any] | None = None


def _resolve_seller(body_name: str = "", body_facts: str = "") -> tuple[str, str]:
    """Body fields win; empty body falls back to persisted shop settings."""
    stored = get_seller_settings()
    name = (body_name or "").strip() or stored.get("seller_name") or ""
    facts = (body_facts or "").strip() or stored.get("seller_facts") or ""
    return normalize_seller_fields(name, facts)


def _ndjson_lines(events: Iterator[dict[str, Any]]) -> Iterator[str]:
    """Serialize outreach stream events as NDJSON (skip internal frames)."""
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "_raw":
            continue
        # Drop non-JSON-serializable accidental keys
        safe = {
            k: v
            for k, v in ev.items()
            if isinstance(v, (str, int, float, bool, list, dict, type(None)))
        }
        yield json.dumps(safe, ensure_ascii=False) + "\n"


def _ndjson_response(events: Iterator[dict[str, Any]]) -> Any:
    return StreamingResponse(
        _ndjson_lines(events),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _persist_outreach_draft_from_done(
    ev: dict[str, Any],
    *,
    client_id: str,
    channel: str,
    status: str = "",
) -> dict[str, Any]:
    """Write Redis/file draft cache when a stream finishes with a message."""
    cid = (client_id or "").strip() or str(ev.get("client_id") or "").strip()
    msg = str(ev.get("message") or "").strip()
    if not cid or not msg:
        return ev
    try:
        set_outreach_draft(
            cid,
            channel or str(ev.get("channel") or "telegram"),
            {
                "message": msg,
                "grounding_notes": ev.get("grounding_notes") or "",
                "source": ev.get("source") or "",
                "status": status
                or "AI сгенерировал креативный текст — можно править вручную.",
                "client_name": ev.get("client_name") or "",
                "title": (
                    f"Черновик · {ev['client_name']}"
                    if ev.get("client_name")
                    else ""
                ),
                "facts": ev.get("facts") if isinstance(ev.get("facts"), dict) else {},
                "sanity": ev.get("sanity") if isinstance(ev.get("sanity"), dict) else None,
            },
        )
        return {
            **ev,
            "cached": True,
            "cache_backend": outreach_cache_backend_name(),
        }
    except Exception as exc:  # pragma: no cover
        log.warning("moysklad outreach draft cache write failed: %s", exc)
        return {**ev, "cached": False, "cache_error": str(exc)}


def _client() -> MoySkladClient:
    if not token_configured():
        raise HTTPException(
            status_code=503,
            detail="MOYSKLAD_API_TOKEN missing. Add it to ~/.hermes/.env.",
        )
    try:
        return MoySkladClient()
    except MoySkladError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _refresh_cached_catalog_counts(
    key: str,
    catalog: dict[str, Any],
    *,
    synced_at: float,
) -> tuple[dict[str, Any], bool]:
    """Recompute tab counts on cache hit; persist when they changed."""
    if not isinstance(catalog, dict):
        return catalog, False
    before = dict(catalog.get("counts") or {})
    after = refresh_audience_counts(catalog)
    if before == after:
        return catalog, False
    try:
        set_cached(key, catalog, synced_at=synced_at or time.time())
    except Exception:
        log.warning("moysklad cache counts rewrite failed", exc_info=True)
        return catalog, True
    return catalog, True


def _invalidate_cache(
    *,
    max_orders: int = 5000,
    max_counterparties: int = 0,
    include_archived: bool = False,
) -> None:
    invalidate(
        cache_key(
            max_orders=max_orders,
            max_counterparties=max_counterparties,
            include_archived=include_archived,
        )
    )


def _strip_internal(page: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in page.items() if not k.startswith("_")}


def _attach_cache_meta(out: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    out.update(meta)
    return out


@router.get("/health")
def health() -> dict[str, Any]:
    if not token_configured():
        return {"ok": False, "error": "MOYSKLAD_API_TOKEN missing"}
    try:
        return MoySkladClient().health()
    except MoySkladError as exc:
        return {"ok": False, "error": str(exc), "status_code": exc.status_code}


@router.get("/clients")
def get_clients(
    sales_filter: str = Query("all"),
    group: str = Query(""),
    q: str = Query(""),
    channel_kind: str = Query(""),
    require_phone: bool = Query(False),
    require_telegram: bool = Query(False),
    vip_only: bool = Query(False),
    birthday_soon: bool = Query(False),
    group_source: str = Query("any"),
    days_before_event: int = Query(0, ge=0, le=365),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    max_orders: int = Query(5000, ge=0, le=100_000),
    max_counterparties: int = Query(0, ge=0, le=100_000),
    include_archived: bool = Query(False),
    refresh: bool = Query(False),
) -> dict[str, Any]:
    snap_key = page_snapshot_key(
        sales_filter=sales_filter,
        group=group,
        q=q,
        group_source=group_source,
        channel_kind=channel_kind,
        require_phone=require_phone,
        require_telegram=require_telegram,
        vip_only=vip_only,
        birthday_soon=birthday_soon,
        days_before_event=days_before_event,
    )
    catalog_key = cache_key(
        max_orders=max_orders,
        max_counterparties=max_counterparties,
        include_archived=include_archived,
    )
    try:
        want_fast = (not refresh) and offset == 0

        # Instant paint: always serve first-100 snapshot when present.
        if want_fast:
            snap_env = get_page_snapshot(snap_key)
            if snap_env is not None:
                sliced = slice_page_snapshot(snap_env, limit=limit, offset=0)
                if sliced is not None:
                    fresh = get_cached(catalog_key)
                    revalidating = False
                    if fresh is None:
                        revalidating = _schedule_catalog_revalidate(
                            catalog_key,
                            max_orders=max_orders,
                            max_counterparties=max_counterparties,
                            include_archived=include_archived,
                        ) or catalog_key in _REVALIDATE_IN_FLIGHT
                    else:
                        revalidating = catalog_key in _REVALIDATE_IN_FLIGHT
                        cat = fresh.get("catalog") if isinstance(fresh, dict) else None
                        _schedule_snapshot_refresh(
                            snap_key,
                            catalog=cat if isinstance(cat, dict) else None,
                            sales_filter=sales_filter,
                            group=group,
                            q=q,
                            group_source=group_source,
                            channel_kind=channel_kind,
                            require_phone=require_phone,
                            require_telegram=require_telegram,
                            vip_only=vip_only,
                            birthday_soon=birthday_soon,
                            days_before_event=days_before_event,
                            max_orders=max_orders,
                            max_counterparties=max_counterparties,
                            include_archived=include_archived,
                        )
                    out_meta = {
                        "cached": True,
                        "stale": bool(revalidating or fresh is None),
                        "revalidating": bool(revalidating),
                        "snapshot": True,
                        "synced_at": float(snap_env.get("synced_at") or 0),
                        "synced_at_label": format_synced_at(
                            float(snap_env.get("synced_at") or 0)
                        ),
                        "cache_ttl_seconds": cache_ttl_seconds(),
                        "cache_backend": cache_backend_name(),
                        "counts_refreshed": False,
                    }
                    return _attach_cache_meta(
                        ensure_group_options_by_source(_strip_internal(sliced)),
                        out_meta,
                    )

        catalog, meta = _get_catalog(
            max_orders=max_orders,
            max_counterparties=max_counterparties,
            include_archived=include_archived,
            force=refresh,
            blocking=not want_fast,
            refresh_counts=not want_fast,
        )

        if catalog is None and want_fast:
            # True cold start — must block once to seed catalog + snapshot.
            catalog, meta = _get_catalog(
                max_orders=max_orders,
                max_counterparties=max_counterparties,
                include_archived=include_archived,
                force=False,
                blocking=True,
                refresh_counts=True,
            )

        if catalog is None:
            raise HTTPException(
                status_code=503,
                detail="catalog unavailable; retry shortly",
            )

        # One-shot Telegram Desktop export → conversations / ТГ ник (+ Redis/file).
        try:
            _apply_telegram_export_and_recache(
                catalog,
                max_orders=max_orders,
                max_counterparties=max_counterparties,
                include_archived=include_archived,
                force_import=False,
            )
        except Exception:
            log.warning("moysklad telegram export hook failed", exc_info=True)

        page = clients_page(
            _client(),
            sales_filter=sales_filter,
            group=group,
            q=q,
            channel_kind=channel_kind,
            require_phone=require_phone,
            require_telegram=require_telegram,
            vip_only=vip_only,
            birthday_soon=birthday_soon,
            group_source=group_source,
            days_before_event=days_before_event,
            limit=limit,
            offset=offset,
            max_orders=max_orders,
            max_counterparties=max_counterparties,
            include_archived=include_archived,
            catalog=catalog,
        )
        page["clients"] = enrich_clients(list(page.get("clients") or []))

        # Seed/refresh first-100 snapshot for this filter (independent of request limit).
        if offset == 0:
            try:
                if int(limit) >= PAGE_SNAPSHOT_ROWS:
                    snap_page = page
                else:
                    snap_page = clients_page(
                        _client(),
                        sales_filter=sales_filter,
                        group=group,
                        q=q,
                        channel_kind=channel_kind,
                        require_phone=require_phone,
                        require_telegram=require_telegram,
                        vip_only=vip_only,
                        birthday_soon=birthday_soon,
                        group_source=group_source,
                        days_before_event=days_before_event,
                        limit=PAGE_SNAPSHOT_ROWS,
                        offset=0,
                        max_orders=max_orders,
                        max_counterparties=max_counterparties,
                        include_archived=include_archived,
                        catalog=catalog,
                    )
                    snap_page["clients"] = enrich_clients(
                        list(snap_page.get("clients") or [])
                    )
                set_page_snapshot(
                    snap_key,
                    _strip_internal(snap_page),
                    synced_at=float(meta.get("synced_at") or time.time()),
                )
            except Exception:
                log.warning("moysklad page snapshot write failed", exc_info=True)

        meta = dict(meta)
        meta["snapshot"] = False
        meta["revalidating"] = bool(meta.get("revalidating"))
        return _attach_cache_meta(
            ensure_group_options_by_source(_strip_internal(page)),
            meta,
        )
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /clients failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/clients/integrity")
def get_clients_integrity(
    max_orders: int = Query(5000, ge=0, le=100_000),
    max_counterparties: int = Query(0, ge=0, le=100_000),
    include_archived: bool = Query(False),
    refresh: bool = Query(False),
) -> dict[str, Any]:
    """Audit tab counts vs hybrid / no-orders / marker-only breakdown."""
    try:
        catalog, meta = _get_catalog(
            max_orders=max_orders,
            max_counterparties=max_counterparties,
            include_archived=include_archived,
            force=refresh,
        )
        report = catalog_integrity(catalog)
        return _attach_cache_meta(report, meta)
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /clients/integrity failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/clients/{client_id}")
def get_client_detail(
    client_id: str,
    max_orders: int = Query(5000, ge=0, le=100_000),
    max_counterparties: int = Query(0, ge=0, le=100_000),
    include_archived: bool = Query(False),
    ai: bool = Query(False),
) -> dict[str, Any]:
    """Client card from durable catalog cache (orders + stats + messaging).

    Pass ``ai=true`` to also run the guarded LLM summary/recommendation.
    Without it, a deterministic heuristic AI block is always included.
    """
    try:
        catalog, meta = _get_catalog(
            max_orders=max_orders,
            max_counterparties=max_counterparties,
            include_archived=include_archived,
            force=False,
        )
        row = find_row_in_catalog(catalog, client_id)
        if row is None:
            raise HTTPException(status_code=404, detail="client not found in catalog")
        detail = build_client_detail(row)
        if ai:
            detail["ai"] = generate_ai_for_detail(detail)
        return _attach_cache_meta(detail, meta)
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /clients/{id} failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/clients/{client_id}/conversation")
def get_client_conversation(client_id: str) -> dict[str, Any]:
    """Local TG/WA thread for a client (MVP store; gateway sync later)."""
    try:
        catalog, meta = _get_catalog(force=False)
        row = find_row_in_catalog(catalog, client_id)
        if row is None:
            # Still allow lookup by id alone (orphaned local thread).
            thread = get_thread(client_id=client_id)
            return {"ok": True, "conversation": thread}
        detail = build_client_detail(row)
        return _attach_cache_meta(
            {"ok": True, "conversation": detail.get("conversation") or {}},
            meta,
        )
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /clients/{id}/conversation failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/clients/{client_id}/conversation")
def post_client_conversation(
    client_id: str, body: ConversationAppendBody
) -> dict[str, Any]:
    """Append a message (outbound/inbound) to the local client thread.

    Outbound Telegram from the client card (``source=client_card_send``) also
    attempts Bot API delivery via the Business outreach bot.
    """
    try:
        text = (body.text or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="text required")
        catalog, _meta = _get_catalog(force=False)
        row = find_row_in_catalog(catalog, client_id)
        phone = ""
        tg_nick = ""
        tg_conversation = ""
        tg_chat_id = ""
        client_name = ""
        deep_link = ""
        if row is not None:
            detail = build_client_detail(row)
            client = detail.get("client") or {}
            phone = str(client.get("phone") or "")
            tg_nick = str(client.get("tg_nick") or "")
            tg_conversation = str(client.get("tg_conversation") or "")
            tg_chat_id = str(client.get("tg_chat_id") or "")
            client_name = str(client.get("name") or "")
            msg = detail.get("messaging") or {}
            ch = (body.channel or "telegram").strip().lower()
            if ch == "whatsapp":
                deep_link = str(msg.get("whatsapp_url") or "")
            else:
                deep_link = str(msg.get("telegram_url") or "")

        channel = (body.channel or "telegram").strip().lower()
        direction = (body.direction or "outbound").strip().lower()
        source = body.source or "manual"
        delivery: dict[str, Any] = {"ok": False, "skipped": True}
        if (
            direction == "outbound"
            and channel.startswith("telegram")
            and source.startswith(("client_card_send", "campaign"))
        ):
            delivery = send_outreach_to_client(
                text=text,
                tg_nick=tg_nick,
                tg_conversation=tg_conversation,
                tg_chat_id=tg_chat_id,
            )
            if delivery.get("ok"):
                source = "client_card_telegram_bot"

        thread = append_message(
            client_id=client_id,
            text=text,
            direction=direction,
            channel=channel,
            label=body.label or "",
            phone=phone,
            tg_nick=tg_nick,
            client_name=client_name,
            source=source,
        )
        open_link = bool(body.open_deep_link) and not delivery.get("ok")
        return {
            "ok": True,
            "conversation": thread,
            "deep_link": deep_link if open_link else "",
            "delivery": delivery,
            "telegram": telegram_send_status(),
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad POST /clients/{id}/conversation failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/campaigns/mark-sent")
def post_campaign_mark_sent(body: MarkSentBody) -> dict[str, Any]:
    """Deliver outreach via Telegram Business bot (when configured) and record thread.

    WhatsApp still returns a deep-link only. Telegram: Bot API send first; on
    failure or missing target, optionally open deep-link for manual send.
    ``client_id`` may be a MoySklad id or ``custom:<id>`` from outreach contacts.
    """
    try:
        text = (body.message or "").strip()
        client_id = (body.client_id or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="message required")
        if not client_id:
            raise HTTPException(status_code=400, detail="client_id required")

        channel = (body.channel or "telegram").strip().lower()
        deep_link = ""
        phone = ""
        tg_nick = ""
        tg_conversation = ""
        tg_chat_id = ""
        client_name = ""
        facts_payload: dict[str, Any] | None = None
        meta: dict[str, Any] = {}

        if client_id.startswith("custom:"):
            contact = get_contact(client_id)
            if contact is None:
                raise HTTPException(status_code=404, detail="custom contact not found")
            tg_nick = str(contact.get("tg_nick") or "")
            tg_chat_id = str(contact.get("tg_chat_id") or "")
            client_name = str(contact.get("name") or "")
            if tg_nick:
                deep_link = f"https://t.me/{tg_nick}"
        else:
            catalog, meta = _get_catalog(force=False)
            row = find_row_in_catalog(catalog, client_id)
            if row is None:
                # Overlay-only peer without catalog row — still allow send.
                contact = get_contact(client_id)
                if contact is None:
                    raise HTTPException(status_code=404, detail="client not found in catalog")
                tg_nick = str(contact.get("tg_nick") or "")
                tg_chat_id = str(contact.get("tg_chat_id") or "")
                client_name = str(contact.get("name") or "")
                if tg_nick:
                    deep_link = f"https://t.me/{tg_nick}"
            else:
                detail = build_client_detail(row)
                client = detail.get("client") or {}
                msg = detail.get("messaging") or {}
                phone = str(client.get("phone") or "")
                tg_nick = str(client.get("tg_nick") or "")
                tg_conversation = str(client.get("tg_conversation") or "")
                tg_chat_id = str(
                    client.get("tg_chat_id")
                    or row.get("ТГ chat id")
                    or row.get("tg_chat_id")
                    or ""
                )
                client_name = str(client.get("name") or "")
                if channel == "whatsapp":
                    deep_link = str(msg.get("whatsapp_url") or "")
                else:
                    deep_link = str(msg.get("telegram_url") or "")
                facts_payload = None  # filled after append

        delivery: dict[str, Any] = {"ok": False, "skipped": True}
        if body.deliver and channel.startswith("telegram"):
            delivery = send_outreach_to_client(
                text=text,
                tg_nick=tg_nick,
                tg_conversation=tg_conversation,
                tg_chat_id=tg_chat_id,
            )

        source = "campaign_send"
        if delivery.get("ok"):
            source = "campaign_telegram_bot"

        thread = append_message(
            client_id=client_id,
            text=text,
            direction="outbound",
            channel=channel,
            label="",
            phone=phone,
            tg_nick=tg_nick,
            client_name=client_name,
            source=source,
        )
        open_link = bool(body.open_deep_link) and not delivery.get("ok")
        payload: dict[str, Any] = {
            "ok": True,
            "conversation": thread,
            "deep_link": deep_link if open_link else "",
            "channel": channel,
            "delivery": delivery,
            "telegram": telegram_send_status(),
        }
        if not client_id.startswith("custom:"):
            catalog_row = None
            try:
                catalog, meta = _get_catalog(force=False)
                catalog_row = find_row_in_catalog(catalog, client_id)
            except Exception:
                catalog_row = None
            if catalog_row is not None:
                detail = build_client_detail(catalog_row)
                payload["facts"] = facts_panel({**detail, "conversation": thread})
                return _attach_cache_meta(payload, meta)
        return payload
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /campaigns/mark-sent failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/campaigns/mark-sent-batch")
def post_campaign_mark_sent_batch(body: MarkSentBatchBody) -> dict[str, Any]:
    """Deliver one message to many clients via Business bot; record each thread."""
    try:
        text = (body.message or "").strip()
        ids = [str(x or "").strip() for x in (body.client_ids or []) if str(x or "").strip()]
        # De-dupe, preserve order
        seen: set[str] = set()
        client_ids: list[str] = []
        for cid in ids:
            if cid in seen:
                continue
            seen.add(cid)
            client_ids.append(cid)
        if not text:
            raise HTTPException(status_code=400, detail="message required")
        if not client_ids:
            raise HTTPException(status_code=400, detail="client_ids required")
        if len(client_ids) > 50:
            raise HTTPException(status_code=400, detail="max 50 clients per batch")

        catalog, meta = _get_catalog(force=False)
        channel = (body.channel or "telegram").strip().lower()
        results: list[dict[str, Any]] = []
        sent_ok = 0
        for client_id in client_ids:
            tg_nick = ""
            tg_conversation = ""
            tg_chat_id = ""
            client_name = ""
            phone = ""
            deep_link = ""

            if client_id.startswith("custom:"):
                contact = get_contact(client_id)
                if contact is None:
                    results.append(
                        {
                            "client_id": client_id,
                            "ok": False,
                            "error": "client_not_found",
                            "delivery": {"ok": False},
                        }
                    )
                    continue
                tg_nick = str(contact.get("tg_nick") or "")
                tg_chat_id = str(contact.get("tg_chat_id") or "")
                client_name = str(contact.get("name") or "")
                if tg_nick:
                    deep_link = f"https://t.me/{tg_nick}"
            else:
                row = find_row_in_catalog(catalog, client_id)
                if row is None:
                    contact = get_contact(client_id)
                    if contact is None:
                        results.append(
                            {
                                "client_id": client_id,
                                "ok": False,
                                "error": "client_not_found",
                                "delivery": {"ok": False},
                            }
                        )
                        continue
                    tg_nick = str(contact.get("tg_nick") or "")
                    tg_chat_id = str(contact.get("tg_chat_id") or "")
                    client_name = str(contact.get("name") or "")
                    if tg_nick:
                        deep_link = f"https://t.me/{tg_nick}"
                else:
                    detail = build_client_detail(row)
                    client = detail.get("client") or {}
                    msg = detail.get("messaging") or {}
                    phone = str(client.get("phone") or "")
                    tg_nick = str(client.get("tg_nick") or "")
                    tg_conversation = str(client.get("tg_conversation") or "")
                    tg_chat_id = str(
                        client.get("tg_chat_id")
                        or row.get("ТГ chat id")
                        or row.get("tg_chat_id")
                        or ""
                    )
                    client_name = str(client.get("name") or "")
                    if channel == "whatsapp":
                        deep_link = str(msg.get("whatsapp_url") or "")
                    else:
                        deep_link = str(msg.get("telegram_url") or "")

            delivery: dict[str, Any] = {"ok": False, "skipped": True}
            if body.deliver and channel.startswith("telegram"):
                delivery = send_outreach_to_client(
                    text=text,
                    tg_nick=tg_nick,
                    tg_conversation=tg_conversation,
                    tg_chat_id=tg_chat_id,
                )

            source = "campaign_send_batch"
            if delivery.get("ok"):
                source = "campaign_telegram_bot"
                sent_ok += 1

            thread = append_message(
                client_id=client_id,
                text=text,
                direction="outbound",
                channel=channel,
                label="",
                phone=phone,
                tg_nick=tg_nick,
                client_name=client_name,
                source=source,
            )
            open_link = bool(body.open_deep_link) and not delivery.get("ok")
            results.append(
                {
                    "client_id": client_id,
                    "ok": True,
                    "client_name": client_name,
                    "delivery": delivery,
                    "conversation": thread,
                    "deep_link": deep_link if open_link else "",
                }
            )

        payload = {
            "ok": True,
            "channel": channel,
            "total": len(client_ids),
            "sent_ok": sent_ok,
            "results": results,
            "telegram": telegram_send_status(),
        }
        return _attach_cache_meta(payload, meta)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /campaigns/mark-sent-batch failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/campaigns/telegram-contacts")
def get_campaign_telegram_contacts(
    q: str = Query(""),
    limit: int = Query(200, ge=1, le=500),
) -> dict[str, Any]:
    """Dropdown contacts: custom + export overlay + catalog with TG."""
    try:
        catalog_clients: list[dict[str, Any]] = []
        try:
            catalog, _meta = _get_catalog(force=False, blocking=False, refresh_counts=False)
            if catalog is not None:
                page = clients_page(
                    _client(),
                    sales_filter="all",
                    require_telegram=True,
                    limit=min(limit, 200),
                    offset=0,
                    catalog=catalog,
                )
                catalog_clients = enrich_clients(list(page.get("clients") or []))
        except Exception:
            log.debug("telegram-contacts catalog preview skipped", exc_info=True)
        contacts = list_outreach_contacts(
            catalog_clients=catalog_clients, q=q, limit=limit
        )
        return {"ok": True, "contacts": contacts, "total": len(contacts)}
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad GET /campaigns/telegram-contacts failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/campaigns/telegram-contacts")
def post_campaign_telegram_contact(body: OutreachContactBody) -> dict[str, Any]:
    """Add a custom outreach contact (@nick / t.me / chat id), resolving via Bot API."""
    try:
        contact = add_custom_contact(
            name=body.name,
            tg_nick=body.tg_nick,
            tg_chat_id=body.tg_chat_id,
            query=body.query,
            resolve=bool(body.resolve),
        )
        return {"ok": True, "contact": contact}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad POST /campaigns/telegram-contacts failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/campaigns/telegram-contacts/resolve")
def post_campaign_telegram_contact_resolve(body: OutreachResolveBody) -> dict[str, Any]:
    """Resolve @nick / t.me / numeric id via getChat + local export overlay."""
    try:
        out = resolve_peer_identity(
            query=body.query,
            tg_nick=body.tg_nick,
            tg_chat_id=body.tg_chat_id,
        )
        if not out.get("ok"):
            raise HTTPException(
                status_code=400,
                detail=str(out.get("detail") or out.get("error") or "resolve failed"),
            )
        return {"ok": True, **out}
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad POST /campaigns/telegram-contacts/resolve failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/campaigns/telegram-contacts/{contact_id}")
def delete_campaign_telegram_contact(contact_id: str) -> dict[str, Any]:
    cid = (contact_id or "").strip()
    if not cid.startswith("custom:"):
        raise HTTPException(status_code=400, detail="only custom contacts can be deleted")
    if not delete_custom_contact(cid):
        raise HTTPException(status_code=404, detail="contact not found")
    return {"ok": True, "deleted": cid}


@router.post("/clients/{client_id}/ai")
def post_client_ai(
    client_id: str,
    body: ClientAiBody | None = None,
    max_orders: int = Query(5000, ge=0, le=100_000),
    max_counterparties: int = Query(0, ge=0, le=100_000),
    include_archived: bool = Query(False),
    provider: str = Query(""),
    model: str = Query(""),
) -> dict[str, Any]:
    """Generate AI summary + sales recommendation for a client card.

    Pass ``provider`` / ``model`` (body or query) to try different LLMs.
    """
    try:
        body = body or ClientAiBody()
        max_orders = body.max_orders or max_orders
        max_counterparties = body.max_counterparties or max_counterparties
        include_archived = bool(body.include_archived or include_archived)
        provider = (body.provider or provider or "").strip()
        model = (body.model or model or "").strip()
        catalog, meta = _get_catalog(
            max_orders=max_orders,
            max_counterparties=max_counterparties,
            include_archived=include_archived,
            force=False,
        )
        row = find_row_in_catalog(catalog, client_id)
        if row is None:
            raise HTTPException(status_code=404, detail="client not found in catalog")
        detail = build_client_detail(row)
        ai_block = generate_ai_for_detail(
            detail,
            provider=provider or None,
            model=model or None,
        )
        return _attach_cache_meta(
            {
                "ok": True,
                "client_id": client_id,
                "ai": ai_block,
                "data_thin": detail.get("data_thin"),
                "provider": provider,
                "model": model,
            },
            meta,
        )
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /clients/{id}/ai failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/clients/{client_id}/conversation/sync")
def post_client_conversation_sync(client_id: str) -> dict[str, Any]:
    """Pull Telegram gateway history into the local client thread."""
    try:
        catalog, meta = _get_catalog(force=False)
        row = find_row_in_catalog(catalog, client_id) if catalog else None
        phone = ""
        tg_nick = ""
        client_name = ""
        if row is not None:
            detail = build_client_detail(row)
            client = detail.get("client") or {}
            phone = str(client.get("phone") or "")
            tg_nick = str(client.get("tg_nick") or "")
            client_name = str(client.get("name") or "")
        thread = sync_from_gateway(
            client_id=client_id,
            phone=phone,
            tg_nick=tg_nick,
            client_name=client_name,
        )
        return _attach_cache_meta({"ok": True, "conversation": thread}, meta)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /clients/{id}/conversation/sync failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/clients/telegram-export/import")
def post_telegram_export_import(
    force: bool = Query(False),
    max_orders: int = Query(5000, ge=0, le=100_000),
    max_counterparties: int = Query(0, ge=0, le=100_000),
    include_archived: bool = Query(False),
) -> dict[str, Any]:
    """Map ``data/telegram_export.json`` chats → TG conversation + ТГ ник."""
    try:
        from plugins.moysklad.telegram_export import import_export_into_catalog

        catalog, meta = _get_catalog(
            max_orders=max_orders,
            max_counterparties=max_counterparties,
            include_archived=include_archived,
            force=False,
            blocking=True,
            refresh_counts=False,
        )
        if catalog is None:
            raise HTTPException(status_code=503, detail="catalog unavailable")
        result = import_export_into_catalog(
            list(catalog.get("rows") or []),
            force=force,
        )
        return _attach_cache_meta(result, meta)
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad telegram-export import failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/clients/ai-fill")
def post_clients_ai_fill(body: AiFillBody) -> dict[str, Any]:
    """Fill empty CRM fields (Группы, Статус, Пол, …) via AI + heuristics.

    Stamps ``ai_fields`` for green AI markers in the Clients table
    (same idea as client_segmentation ``.ai-cell-new``).

    Pass ``ids`` for lazy evaluation of the visible page; Redis/file cache
    skips LLM on repeat unless ``force=true``.
    """
    try:
        catalog, meta = _get_catalog(
            max_orders=body.max_orders,
            max_counterparties=body.max_counterparties,
            include_archived=body.include_archived,
            force=False,
        )
        # When ids are provided (lazy page fill), scan the full catalog rows
        # so we can resolve those counterparties without a huge filtered page.
        if body.ids:
            rows = list(catalog.get("rows") or [])
        else:
            page = clients_page(
                _client(),
                sales_filter=body.sales_filter,
                group=body.group,
                q=body.q,
                limit=500,
                offset=0,
                max_orders=body.max_orders,
                max_counterparties=body.max_counterparties,
                include_archived=body.include_archived,
                catalog=catalog,
            )
            rows = list(page.get("_rows") or catalog.get("rows") or [])
        result = fill_empty_for_rows(
            rows,
            client_ids=list(body.ids or []),
            limit=body.limit,
            use_llm=bool(body.use_llm),
            force=bool(body.force),
        )
        result["ai_fill_cache_backend"] = ai_fill_cache_backend_name()
        return _attach_cache_meta(result, meta)
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /clients/ai-fill failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/eval/golden-clients")
def get_eval_golden_clients() -> dict[str, Any]:
    """List ~20 golden eval clients for the AI playground."""
    try:
        return list_golden_clients()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /eval/golden-clients failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/eval/golden-clients/{client_id}")
def get_eval_golden_client(client_id: str) -> dict[str, Any]:
    """Raw golden fixture row + playground stages (heuristic only)."""
    try:
        golden = get_golden_client(client_id)
        trace = run_playground(client_id=client_id, run_llm=False)
        return {"ok": True, "client": golden, **trace}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /eval/golden-clients/{id} failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/eval/playground/run")
def post_eval_playground_run(body: PlaygroundRunBody) -> dict[str, Any]:
    """Run AI playground stages (optional LLM) from golden id or edited JSON."""
    try:
        return run_playground(
            client_id=body.client_id or "",
            input_json=body.input_json or "",
            run_llm=bool(body.run_llm),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /eval/playground/run failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/sync")
def post_sync(
    max_orders: int = Query(5000, ge=0, le=100_000),
    max_counterparties: int = Query(0, ge=0, le=100_000),
    include_archived: bool = Query(False),
) -> dict[str, Any]:
    """Force re-download from MoySklad and refresh durable cache."""
    try:
        catalog, meta = _get_catalog(
            max_orders=max_orders,
            max_counterparties=max_counterparties,
            include_archived=include_archived,
            force=True,
        )
        return {
            "ok": True,
            "synced": True,
            "counterparties_scanned": catalog.get("counterparties_scanned", 0),
            "orders_scanned": catalog.get("orders_scanned", 0),
            "counts": catalog.get("counts") or {},
            **meta,
        }
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /sync failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/groups/assign")
def post_groups_assign(body: AssignBody) -> dict[str, Any]:
    try:
        catalog, _meta = _get_catalog(
            max_orders=body.max_orders,
            max_counterparties=body.max_counterparties,
            include_archived=body.include_archived,
        )
        page = clients_page(
            _client(),
            sales_filter=body.sales_filter,
            group=body.group,
            q=body.q,
            limit=500,
            offset=0,
            catalog=catalog,
        )
        # Assign against the current filter (matched rows), not only the page slice.
        rows = list(page.get("_rows") or [])
        proposals = propose_groups_for_rows(rows, counterparty_ids=body.ids or None)
        changed = [p for p in proposals if p.get("changed")]
        result: dict[str, Any] = {
            "ok": True,
            "dry_run": bool(body.dry_run),
            "total": len(proposals),
            "changed": len(changed),
            "assignments": proposals if body.ids else changed,
        }
        if not body.dry_run:
            push = push_merged_tags(_client(), changed, only_changed=True)
            result["push"] = push
            if push.get("pushed"):
                _invalidate_cache(
                    max_orders=body.max_orders,
                    max_counterparties=body.max_counterparties,
                    include_archived=body.include_archived,
                )
        return result
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /groups/assign failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/groups/push")
def post_groups_push(body: PushBody) -> dict[str, Any]:
    try:
        if not body.assignments:
            raise HTTPException(status_code=400, detail="assignments required")
        push = push_merged_tags(
            _client(),
            body.assignments,
            only_changed=body.only_changed,
        )
        if push.get("pushed"):
            _invalidate_cache()
        return push
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /groups/push failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/groups/taxonomy")
def get_groups_taxonomy() -> dict[str, Any]:
    return {"ok": True, "groups": load_taxonomy()}


@router.post("/groups/recalculate/propose")
def post_groups_recalculate_propose(body: RecalculateProposeBody) -> dict[str, Any]:
    """LLM (or heuristic) proposes a new group taxonomy for the current audience."""
    try:
        catalog, meta = _get_catalog(
            max_orders=body.max_orders,
            max_counterparties=body.max_counterparties,
            include_archived=body.include_archived,
        )
        page = clients_page(
            _client(),
            sales_filter=body.sales_filter,
            group=body.group,
            q=body.q,
            channel_kind=body.channel_kind,
            require_phone=body.require_phone,
            require_telegram=body.require_telegram,
            vip_only=body.vip_only,
            birthday_soon=body.birthday_soon,
            group_source=getattr(body, 'group_source', 'any') or 'any',
            days_before_event=int(getattr(body, 'days_before_event', 0) or 0),
            limit=0,
            offset=0,
            catalog=catalog,
        )
        rows = list(page.get("_rows") or [])
        proposal = propose_taxonomy(rows, sales_filter=body.sales_filter)
        proposal["cached"] = meta.get("cached")
        return proposal
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /groups/recalculate/propose failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/groups/recalculate/apply")
def post_groups_recalculate_apply(body: RecalculateApplyBody) -> dict[str, Any]:
    """Persist taxonomy and reassign labels; optional push to MoySklad."""
    try:
        if not body.groups:
            raise HTTPException(status_code=400, detail="groups required")
        saved = save_taxonomy(body.groups)
        catalog, _meta = _get_catalog(
            max_orders=body.max_orders,
            max_counterparties=body.max_counterparties,
            include_archived=body.include_archived,
        )
        page = clients_page(
            _client(),
            sales_filter=body.sales_filter,
            group=body.group,
            q=body.q,
            channel_kind=body.channel_kind,
            require_phone=body.require_phone,
            require_telegram=body.require_telegram,
            vip_only=body.vip_only,
            birthday_soon=body.birthday_soon,
            group_source=getattr(body, 'group_source', 'any') or 'any',
            days_before_event=int(getattr(body, 'days_before_event', 0) or 0),
            limit=0,
            offset=0,
            catalog=catalog,
        )
        rows = list(page.get("_rows") or [])
        assignments = assign_to_taxonomy(rows, saved)
        changed = [a for a in assignments if a.get("changed")]
        result: dict[str, Any] = {
            "ok": True,
            "dry_run": bool(body.dry_run),
            "groups": saved,
            "total": len(assignments),
            "changed": len(changed),
            "assignments": changed[:200],
        }
        if not body.dry_run and body.push:
            push = push_merged_tags(_client(), changed, only_changed=True)
            result["push"] = push
            if push.get("pushed"):
                _invalidate_cache(
                    max_orders=body.max_orders,
                    max_counterparties=body.max_counterparties,
                    include_archived=body.include_archived,
                )
        elif not body.dry_run:
            # Taxonomy saved; invalidate so facet counts refresh on next read.
            _invalidate_cache(
                max_orders=body.max_orders,
                max_counterparties=body.max_counterparties,
                include_archived=body.include_archived,
            )
        return result
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /groups/recalculate/apply failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/campaigns")
def get_campaigns() -> dict[str, Any]:
    return {"ok": True, "campaigns": list_campaigns()}


@router.get("/campaigns/seller-settings")
def get_campaign_seller_settings() -> dict[str, Any]:
    settings = get_seller_settings()
    return {
        "ok": True,
        **settings,
        "telegram": telegram_send_status(),
        "telegram_account": telegram_account_snapshot(
            settings.get("telegram_business_connection_id") or None
        ),
    }


@router.put("/campaigns/seller-settings")
def put_campaign_seller_settings(body: SellerSettingsBody) -> dict[str, Any]:
    saved = save_seller_settings(
        seller_name=body.seller_name,
        seller_facts=body.seller_facts,
        telegram_business_connection_id=body.telegram_business_connection_id,
    )
    return {
        "ok": True,
        **saved,
        "telegram": telegram_send_status(),
        "telegram_account": telegram_account_snapshot(
            saved.get("telegram_business_connection_id") or None
        ),
    }


@router.get("/campaigns/telegram-account")
def get_campaign_telegram_account() -> dict[str, Any]:
    """Probe Business connection for campaigns UI (account @nick / rights)."""
    settings = get_seller_settings()
    snap = telegram_account_snapshot(
        settings.get("telegram_business_connection_id") or None
    )
    return {"ok": True, **snap}


@router.get("/campaigns/draft-cache")
def get_campaign_draft_cache(
    client_id: str = Query(""),
    channel: str = Query("telegram"),
) -> dict[str, Any]:
    """Load durable outreach draft (Redis → file) for a client + channel."""
    cid = (client_id or "").strip()
    if not cid:
        raise HTTPException(status_code=400, detail="client_id required")
    draft = get_outreach_draft(cid, channel)
    return {
        "ok": True,
        "hit": draft is not None,
        "draft": draft,
        "cache_backend": outreach_cache_backend_name(),
    }


@router.put("/campaigns/draft-cache")
def put_campaign_draft_cache(body: OutreachDraftCacheBody) -> dict[str, Any]:
    """Persist outreach draft to Redis/file (manual save / button results)."""
    cid = (body.client_id or "").strip()
    if not cid:
        raise HTTPException(status_code=400, detail="client_id required")
    message = (body.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message required")
    try:
        envelope = set_outreach_draft(
            cid,
            body.channel,
            {
                "message": message,
                "grounding_notes": body.grounding_notes,
                "source": body.source,
                "status": body.status,
                "client_name": body.client_name,
                "title": body.title,
                "facts": body.facts or {},
                "sanity": body.sanity,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "draft": envelope.get("draft"),
        "saved_at": envelope.get("saved_at"),
        "cache_backend": outreach_cache_backend_name(),
    }


@router.post("/campaigns/generate")
def post_campaign_generate(body: OutreachGenerateBody) -> dict[str, Any]:
    """AI (or heuristic) outreach text + facts panel for one client.

    Uses the same durable catalog cache as /clients (marketplace/direct).
    Serves Redis/file draft cache unless ``refresh_ai`` forces a new LLM pass.
    """
    try:
        client_id = (body.client_id or "").strip()
        if not client_id:
            raise HTTPException(status_code=400, detail="client_id required")
        catalog, meta = _get_catalog(
            max_orders=body.max_orders,
            max_counterparties=body.max_counterparties,
            include_archived=body.include_archived,
            force=False,
        )
        row = find_row_in_catalog(catalog, client_id)
        if row is None:
            raise HTTPException(status_code=404, detail="client not found in catalog")
        seller_name, seller_facts = _resolve_seller(body.seller_name, body.seller_facts)
        if (body.seller_name or "").strip() or (body.seller_facts or "").strip():
            save_seller_settings(seller_name=seller_name, seller_facts=seller_facts)
        force = bool(body.refresh_ai)
        if not force:
            cached = get_outreach_draft(client_id, body.channel)
            msg = str((cached or {}).get("message") or "").strip()
            if cached and msg:
                return _attach_cache_meta(
                    {
                        "ok": True,
                        "message": msg,
                        "grounding_notes": cached.get("grounding_notes") or "",
                        "source": cached.get("source") or "redis-cache",
                        "from_cache": True,
                        "cached": True,
                        "facts": cached.get("facts")
                        if isinstance(cached.get("facts"), dict)
                        else {},
                        "sanity": cached.get("sanity")
                        if isinstance(cached.get("sanity"), dict)
                        else None,
                        "client_id": client_id,
                        "client_name": cached.get("client_name") or "",
                        "seller_name": seller_name,
                        "seller_facts": seller_facts,
                        "channel": body.channel,
                        "cache_backend": outreach_cache_backend_name(),
                    },
                    meta,
                )
        outreach = build_outreach_for_row(
            row,
            channel=body.channel,
            refresh_ai=force,
            seller_name=seller_name,
            seller_facts=seller_facts,
            use_draft_cache=True,
            force_refresh=force,
        )
        return _attach_cache_meta(
            {
                "ok": True,
                **outreach,
                "cache_backend": outreach_cache_backend_name(),
            },
            meta,
        )
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /campaigns/generate failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/campaigns/generate/stream")
def post_campaign_generate_stream(body: OutreachGenerateBody) -> Any:
    """NDJSON stream: status → delta* → (replace?) → done.

    First status event is emitted before catalog I/O so the UI is not stuck
    on a silent wait. Tokens stream as plain text (or ``message`` JSON field).
    Cached drafts short-circuit when ``refresh_ai`` is false.
    """
    client_id = (body.client_id or "").strip()
    if not client_id:
        raise HTTPException(status_code=400, detail="client_id required")
    seller_name, seller_facts = _resolve_seller(body.seller_name, body.seller_facts)
    if (body.seller_name or "").strip() or (body.seller_facts or "").strip():
        save_seller_settings(seller_name=seller_name, seller_facts=seller_facts)
    force = bool(body.refresh_ai)

    def _events() -> Iterator[dict[str, Any]]:
        if not force:
            cached = get_outreach_draft(client_id, body.channel)
            msg = str((cached or {}).get("message") or "").strip()
            if cached and msg:
                yield {"type": "status", "text": "Из кэша Redis/файл…"}
                yield {
                    "type": "done",
                    "ok": True,
                    "message": msg,
                    "grounding_notes": cached.get("grounding_notes") or "",
                    "source": cached.get("source") or "redis-cache",
                    "from_cache": True,
                    "cached": True,
                    "cache_backend": outreach_cache_backend_name(),
                    "facts": cached.get("facts")
                    if isinstance(cached.get("facts"), dict)
                    else {},
                    "sanity": cached.get("sanity")
                    if isinstance(cached.get("sanity"), dict)
                    else None,
                    "client_id": client_id,
                    "client_name": cached.get("client_name") or "",
                    "channel": body.channel,
                }
                return

        yield {"type": "status", "text": "Генерируем креативный текст…"}
        try:
            catalog, _meta = _get_catalog(
                max_orders=body.max_orders,
                max_counterparties=body.max_counterparties,
                include_archived=body.include_archived,
                force=False,
            )
            row = find_row_in_catalog(catalog, client_id)
            if row is None:
                yield {"type": "error", "error": "client not found in catalog"}
                return
            for ev in iter_generate_outreach_for_row_events(
                row,
                channel=body.channel,
                refresh_ai=force,
                seller_name=seller_name,
                seller_facts=seller_facts,
            ):
                if ev.get("type") == "status":
                    continue  # already emitted above
                if ev.get("type") == "done":
                    ev = _persist_outreach_draft_from_done(
                        ev,
                        client_id=client_id,
                        channel=body.channel,
                        status="AI сгенерировал креативный текст — можно править вручную.",
                    )
                yield ev
        except MoySkladError as exc:
            yield {"type": "error", "error": str(exc)}
        except Exception as exc:  # pragma: no cover
            log.exception("moysklad /campaigns/generate/stream failed mid-stream")
            yield {"type": "error", "error": str(exc)}

    return _ndjson_response(_events())

@router.post("/campaigns/rewrite")
def post_campaign_rewrite(body: OutreachRewriteBody) -> dict[str, Any]:
    """Rewrite current draft to be more sales-oriented and human."""
    try:
        draft = (body.message or "").strip()
        if not draft:
            raise HTTPException(status_code=400, detail="message required")
        seller_name, seller_facts = _resolve_seller(body.seller_name, body.seller_facts)
        if (body.seller_name or "").strip() or (body.seller_facts or "").strip():
            save_seller_settings(seller_name=seller_name, seller_facts=seller_facts)
        detail: dict[str, Any] | None = None
        client_id = (body.client_id or "").strip()
        meta = None
        if client_id:
            catalog, meta = _get_catalog(
                max_orders=body.max_orders,
                max_counterparties=body.max_counterparties,
                include_archived=body.include_archived,
                force=False,
            )
            row = find_row_in_catalog(catalog, client_id)
            if row is not None:
                detail = build_client_detail(row)
        result = rewrite_outreach_message(
            draft,
            channel=body.channel,
            seller_name=seller_name,
            seller_facts=seller_facts,
            detail=detail,
        )
        if meta is not None:
            return _attach_cache_meta({"ok": True, **result}, meta)
        return {"ok": True, **result}
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /campaigns/rewrite failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/campaigns/rewrite/stream")
def post_campaign_rewrite_stream(body: OutreachRewriteBody) -> Any:
    """NDJSON stream for rewrite (same event shape as generate/stream)."""
    draft = (body.message or "").strip()
    if not draft:
        raise HTTPException(status_code=400, detail="message required")
    seller_name, seller_facts = _resolve_seller(body.seller_name, body.seller_facts)
    if (body.seller_name or "").strip() or (body.seller_facts or "").strip():
        save_seller_settings(seller_name=seller_name, seller_facts=seller_facts)
    client_id = (body.client_id or "").strip()

    def _events() -> Iterator[dict[str, Any]]:
        yield {"type": "status", "text": "Переписываем продающе…"}
        try:
            detail: dict[str, Any] | None = None
            if client_id:
                catalog, _meta = _get_catalog(
                    max_orders=body.max_orders,
                    max_counterparties=body.max_counterparties,
                    include_archived=body.include_archived,
                    force=False,
                )
                row = find_row_in_catalog(catalog, client_id)
                if row is not None:
                    detail = build_client_detail(row)
            for ev in iter_rewrite_outreach_events(
                draft,
                channel=body.channel,
                seller_name=seller_name,
                seller_facts=seller_facts,
                detail=detail,
            ):
                if ev.get("type") == "status":
                    continue
                if ev.get("type") == "done":
                    ev = _persist_outreach_draft_from_done(
                        ev,
                        client_id=client_id,
                        channel=body.channel,
                        status="Текст обновлён: продающе и по-человечески.",
                    )
                yield ev
        except MoySkladError as exc:
            yield {"type": "error", "error": str(exc)}
        except Exception as exc:  # pragma: no cover
            log.exception("moysklad /campaigns/rewrite/stream failed mid-stream")
            yield {"type": "error", "error": str(exc)}

    return _ndjson_response(_events())


@router.post("/campaigns/suggest-bouquet")
def post_campaign_suggest_bouquet(body: OutreachGenerateBody) -> dict[str, Any]:
    """Suggest a concrete bouquet from the client's order history."""
    try:
        client_id = (body.client_id or "").strip()
        if not client_id:
            raise HTTPException(status_code=400, detail="client_id required")
        catalog, meta = _get_catalog(
            max_orders=body.max_orders,
            max_counterparties=body.max_counterparties,
            include_archived=body.include_archived,
            force=False,
        )
        row = find_row_in_catalog(catalog, client_id)
        if row is None:
            raise HTTPException(status_code=404, detail="client not found in catalog")
        seller_name, seller_facts = _resolve_seller(body.seller_name, body.seller_facts)
        if (body.seller_name or "").strip() or (body.seller_facts or "").strip():
            save_seller_settings(seller_name=seller_name, seller_facts=seller_facts)
        detail = build_client_detail(row)
        result = suggest_historical_bouquet_message(
            detail,
            channel=body.channel,
            seller_name=seller_name,
            seller_facts=seller_facts,
        )
        result["client_id"] = (detail.get("client") or {}).get("id")
        result["client_name"] = (detail.get("client") or {}).get("name")
        return _attach_cache_meta({"ok": True, **result}, meta)
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /campaigns/suggest-bouquet failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/campaigns/suggest-bouquet/stream")
def post_campaign_suggest_bouquet_stream(body: OutreachGenerateBody) -> Any:
    """NDJSON stream for historical bouquet suggestion."""
    client_id = (body.client_id or "").strip()
    if not client_id:
        raise HTTPException(status_code=400, detail="client_id required")
    seller_name, seller_facts = _resolve_seller(body.seller_name, body.seller_facts)
    if (body.seller_name or "").strip() or (body.seller_facts or "").strip():
        save_seller_settings(seller_name=seller_name, seller_facts=seller_facts)

    def _events() -> Iterator[dict[str, Any]]:
        yield {"type": "status", "text": "Подбираем букет из истории…"}
        try:
            catalog, _meta = _get_catalog(
                max_orders=body.max_orders,
                max_counterparties=body.max_counterparties,
                include_archived=body.include_archived,
                force=False,
            )
            row = find_row_in_catalog(catalog, client_id)
            if row is None:
                yield {"type": "error", "error": "client not found in catalog"}
                return
            detail = build_client_detail(row)
            client_id_out = (detail.get("client") or {}).get("id")
            client_name_out = (detail.get("client") or {}).get("name")
            for ev in iter_suggest_bouquet_events(
                detail,
                channel=body.channel,
                seller_name=seller_name,
                seller_facts=seller_facts,
            ):
                if ev.get("type") == "status":
                    continue
                if ev.get("type") == "done":
                    ev = {
                        **ev,
                        "client_id": client_id_out,
                        "client_name": client_name_out,
                    }
                    ev = _persist_outreach_draft_from_done(
                        ev,
                        client_id=str(client_id_out or client_id),
                        channel=body.channel,
                        status="Предложен конкретный букет из истории заказов.",
                    )
                yield ev
        except MoySkladError as exc:
            yield {"type": "error", "error": str(exc)}
        except Exception as exc:  # pragma: no cover
            log.exception("moysklad /campaigns/suggest-bouquet/stream mid-stream")
            yield {"type": "error", "error": str(exc)}

    return _ndjson_response(_events())


@router.post("/campaigns/paraphrase")
def post_campaign_paraphrase(body: OutreachRewriteBody) -> dict[str, Any]:
    """Full paraphrase — must differ from generate and sales rewrite."""
    try:
        draft = (body.message or "").strip()
        if not draft:
            raise HTTPException(status_code=400, detail="message required")
        seller_name, seller_facts = _resolve_seller(body.seller_name, body.seller_facts)
        if (body.seller_name or "").strip() or (body.seller_facts or "").strip():
            save_seller_settings(seller_name=seller_name, seller_facts=seller_facts)
        detail: dict[str, Any] | None = None
        client_id = (body.client_id or "").strip()
        meta = None
        if client_id:
            catalog, meta = _get_catalog(
                max_orders=body.max_orders,
                max_counterparties=body.max_counterparties,
                include_archived=body.include_archived,
                force=False,
            )
            row = find_row_in_catalog(catalog, client_id)
            if row is not None:
                detail = build_client_detail(row)
        result = paraphrase_outreach_message(
            draft,
            channel=body.channel,
            seller_name=seller_name,
            seller_facts=seller_facts,
            detail=detail,
        )
        if meta is not None:
            return _attach_cache_meta({"ok": True, **result}, meta)
        return {"ok": True, **result}
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /campaigns/paraphrase failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/campaigns/paraphrase/stream")
def post_campaign_paraphrase_stream(body: OutreachRewriteBody) -> Any:
    """NDJSON stream for full paraphrase."""
    draft = (body.message or "").strip()
    if not draft:
        raise HTTPException(status_code=400, detail="message required")
    seller_name, seller_facts = _resolve_seller(body.seller_name, body.seller_facts)
    if (body.seller_name or "").strip() or (body.seller_facts or "").strip():
        save_seller_settings(seller_name=seller_name, seller_facts=seller_facts)
    client_id = (body.client_id or "").strip()

    def _events() -> Iterator[dict[str, Any]]:
        yield {"type": "status", "text": "Делаем полную парафразу…"}
        try:
            detail: dict[str, Any] | None = None
            if client_id:
                catalog, _meta = _get_catalog(
                    max_orders=body.max_orders,
                    max_counterparties=body.max_counterparties,
                    include_archived=body.include_archived,
                    force=False,
                )
                row = find_row_in_catalog(catalog, client_id)
                if row is not None:
                    detail = build_client_detail(row)
            for ev in iter_paraphrase_outreach_events(
                draft,
                channel=body.channel,
                seller_name=seller_name,
                seller_facts=seller_facts,
                detail=detail,
            ):
                if ev.get("type") == "status":
                    continue
                if ev.get("type") == "done":
                    ev = _persist_outreach_draft_from_done(
                        ev,
                        client_id=client_id,
                        channel=body.channel,
                        status="Полная парафраза: формулировки сменены, факты те же.",
                    )
                yield ev
        except MoySkladError as exc:
            yield {"type": "error", "error": str(exc)}
        except Exception as exc:  # pragma: no cover
            log.exception("moysklad /campaigns/paraphrase/stream mid-stream")
            yield {"type": "error", "error": str(exc)}

    return _ndjson_response(_events())


@router.post("/campaigns/personalize/stream")
def post_campaign_personalize_stream(body: OutreachPersonalizeBody) -> Any:
    """Batch NDJSON: batch_start → client_done* → batch_done (parallel LLM)."""
    try:
        limit = max(1, min(int(body.limit or 20), 50))
        workers = max(1, min(int(body.max_workers or 3), 5))
        catalog, _meta = _get_catalog(
            max_orders=body.max_orders,
            max_counterparties=body.max_counterparties,
            include_archived=body.include_archived,
            force=False,
        )
        page = clients_page(
            _client(),
            sales_filter=body.sales_filter or "all",
            group=body.group or "",
            q=body.q or "",
            channel_kind=body.channel_kind or "",
            require_phone=bool(body.require_phone),
            require_telegram=bool(body.require_telegram),
            vip_only=bool(body.vip_only),
            birthday_soon=bool(body.birthday_soon),
            group_source=getattr(body, 'group_source', 'any') or 'any',
            days_before_event=int(getattr(body, 'days_before_event', 0) or 0),
            limit=limit,
            offset=0,
            max_orders=body.max_orders,
            max_counterparties=body.max_counterparties,
            include_archived=body.include_archived,
            catalog=catalog,
        )
        rows = list(page.get("clients") or [])[:limit]
        seller_name, seller_facts = _resolve_seller(body.seller_name, body.seller_facts)
        if (body.seller_name or "").strip() or (body.seller_facts or "").strip():
            save_seller_settings(seller_name=seller_name, seller_facts=seller_facts)

        def _events() -> Iterator[dict[str, Any]]:
            try:
                yield from iter_personalize_batch_events(
                    rows,
                    channel=body.channel,
                    seller_name=seller_name,
                    seller_facts=seller_facts,
                    max_workers=workers,
                )
            except Exception as exc:  # pragma: no cover
                log.exception("moysklad /campaigns/personalize/stream failed")
                yield {"type": "error", "error": str(exc)}

        return _ndjson_response(_events())
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /campaigns/personalize/stream setup failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/campaigns/sanity")
def post_campaign_sanity(body: OutreachSanityBody) -> dict[str, Any]:
    """Second-pass meaning check: debt/risk must not produce flower upsell."""
    try:
        draft = (body.message or "").strip()
        if not draft:
            raise HTTPException(status_code=400, detail="message required")
        seller_name, seller_facts = _resolve_seller(body.seller_name, body.seller_facts)
        detail: dict[str, Any] | None = None
        client_id = (body.client_id or "").strip()
        meta = None
        if client_id:
            catalog, meta = _get_catalog(
                max_orders=body.max_orders,
                max_counterparties=body.max_counterparties,
                include_archived=body.include_archived,
                force=False,
            )
            row = find_row_in_catalog(catalog, client_id)
            if row is not None:
                detail = build_client_detail(row)
        sanity = sanity_check_outreach_message(
            draft,
            detail,
            channel=body.channel,
            seller_name=seller_name,
            seller_facts=seller_facts,
        )
        message = draft
        if body.apply_revision and not sanity.get("ok") and sanity.get("revised_text"):
            message = str(sanity["revised_text"])
            sanity = {**sanity, "auto_revised": True}
        payload = {
            "ok": True,
            "message": message,
            "sanity": sanity,
            "facts": facts_panel(detail) if detail else {},
            "seller_name": seller_name,
            "seller_facts": seller_facts,
            "channel": (body.channel or "telegram").strip().lower(),
        }
        if meta is not None:
            return _attach_cache_meta(payload, meta)
        return payload
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /campaigns/sanity failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/campaigns")
def post_campaign(body: CampaignCreateBody) -> dict[str, Any]:
    """Create a draft. Audience filters share the Clients catalog cache.

    Pass ``client_id`` for a personalized 1:1 draft (facts + optional AI text).
    ``mode=auto`` without offer runs outreach generation when client_id is set.
    """
    try:
        catalog, _meta = _get_catalog(
            max_orders=body.max_orders,
            max_counterparties=body.max_counterparties,
            include_archived=body.include_archived,
        )
        client_id = (body.client_id or "").strip()
        facts: dict[str, Any] = {}
        recommendation = ""
        grounding_notes = ""
        ai_source = ""
        client_name = ""
        offer = body.offer
        preview: list[dict[str, Any]] = []
        audience_count = 0
        sales_filter = body.sales_filter or "all"

        if client_id:
            row = find_row_in_catalog(catalog, client_id)
            if row is None:
                raise HTTPException(
                    status_code=404, detail="client not found in catalog"
                )
            detail = build_client_detail(row)
            client = detail.get("client") or {}
            client_name = str(client.get("name") or "")
            sales_filter = str(client.get("sales_type") or sales_filter)
            # Normalize UI filter ids when sales_type is a display label.
            st = sales_filter.lower().replace("ё", "е")
            if "маркет" in st:
                sales_filter = "marketplace"
            elif "прям" in st:
                sales_filter = "direct"
            facts = facts_panel(detail)
            recommendation = str((detail.get("ai") or {}).get("recommendation") or "")
            seller_name, seller_facts = _resolve_seller(
                body.seller_name, body.seller_facts
            )
            if (body.seller_name or "").strip() or (body.seller_facts or "").strip():
                save_seller_settings(
                    seller_name=seller_name, seller_facts=seller_facts
                )
            want_ai = body.mode == "auto" or body.generate_ai or not (offer or "").strip()
            if want_ai and not (offer or "").strip():
                outreach = generate_outreach_message(
                    detail,
                    channel=body.channel,
                    refresh_ai=True,
                    seller_name=seller_name,
                    seller_facts=seller_facts,
                )
                offer = outreach.get("message") or ""
                facts = outreach.get("facts") or facts
                grounding_notes = str(outreach.get("grounding_notes") or "")
                ai_source = str(outreach.get("source") or "")
                recommendation = str(
                    (outreach.get("ai") or detail.get("ai") or {}).get(
                        "recommendation"
                    )
                    or recommendation
                )
            preview = [
                {
                    "id": client.get("id"),
                    "name": client_name,
                    "phone": client.get("phone"),
                    "email": client.get("email"),
                    "sales_type": client.get("sales_type"),
                }
            ]
            audience_count = 1
            title = body.title
            if not (title or "").strip() or title == "Рассылка":
                title = f"Черновик · {client_name or client_id}"
        else:
            page = clients_page(
                _client(),
                sales_filter=body.sales_filter,
                group=body.group,
                q=body.q,
                channel_kind=body.channel_kind,
                require_phone=body.require_phone,
                require_telegram=body.require_telegram,
                vip_only=body.vip_only,
                birthday_soon=body.birthday_soon,
                limit=20 if body.include_preview else 1,
                offset=0,
                catalog=catalog,
            )
            sales_filter = page.get("sales_filter") or body.sales_filter
            audience_count = int(page.get("matched_total") or 0)
            if body.include_preview:
                for row in page.get("clients") or []:
                    preview.append(
                        {
                            "id": row.get("id"),
                            "name": row.get("name"),
                            "phone": row.get("phone"),
                            "email": row.get("email"),
                            "sales_type": row.get("sales_type"),
                        }
                    )
            if body.mode == "auto" and not (offer or "").strip():
                # Mass filter auto: shared template for the group (not per-client).
                seller_name, _seller_facts = _resolve_seller(
                    body.seller_name, body.seller_facts
                )
                intro = (
                    f"Это {seller_name}"
                    if seller_name
                    else "Пишем из цветочного магазина"
                )
                tag_bit = (
                    f' по тегу «{body.group}»'
                    if (body.group or "").strip()
                    else ""
                )
                occasion = " к предстоящему поводу" if body.birthday_soon else ""
                offer = (
                    f"Здравствуйте! {intro}{tag_bit}{occasion}. "
                    "Если удобно подобрать букет — напишите, подберём спокойно. "
                    f"(Общий текст для аудитории {audience_count} чел.; "
                    "персонализация по клиентам — отдельным шагом.)"
                )
                ai_source = "filter_group_stub"
            title = body.title
            if not (title or "").strip() or title in ("Рассылка", "Рассылка по фильтрам"):
                bits = [sales_filter]
                if body.channel_kind:
                    bits.append(body.channel_kind)
                if body.group:
                    bits.append(body.group)
                if body.birthday_soon:
                    bits.append("др/событие")
                if body.vip_only:
                    bits.append("VIP")
                title = "Массовая · " + " · ".join(bits)

        audience_filters = {
            "sales_filter": sales_filter,
            "group": body.group or "",
            "q": body.q or "",
            "channel_kind": body.channel_kind or "",
            "require_phone": bool(body.require_phone),
            "require_telegram": bool(body.require_telegram),
            "vip_only": bool(body.vip_only),
            "birthday_soon": bool(body.birthday_soon),
            "personalize": bool(body.personalize),
        }

        item = create_draft(
            title=title,
            channel=body.channel,
            mode=body.mode,
            offer=offer,
            sales_filter=sales_filter,
            group=body.group,
            q=body.q,
            audience_count=audience_count,
            audience_preview=preview,
            audience_filters=audience_filters,
            client_id=client_id,
            client_name=client_name,
            facts=facts,
            recommendation=recommendation,
            grounding_notes=grounding_notes,
            ai_source=ai_source,
            personalize_pending=bool(body.personalize) and not client_id,
        )
        return {"ok": True, "campaign": item}
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /campaigns create failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/campaigns/{campaign_id}")
def remove_campaign(campaign_id: str) -> dict[str, Any]:
    ok = delete_campaign(campaign_id)
    if not ok:
        raise HTTPException(status_code=404, detail="campaign not found")
    return {"ok": True, "deleted": campaign_id}
