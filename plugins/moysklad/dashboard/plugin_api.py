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
    send_outreach_to_client,
    telegram_send_status,
)
from plugins.moysklad.catalog_cache import (
    cache_backend_name,
    cache_key,
    cache_ttl_seconds,
    format_synced_at,
    get_cached,
    invalidate,
    set_cached,
)
from plugins.moysklad.classify import build_enriched_catalog, clients_page
from plugins.moysklad.client import MoySkladClient, MoySkladError, token_configured
from plugins.moysklad.client_card import (
    build_client_detail,
    find_row_in_catalog,
    generate_ai_for_detail,
)
from plugins.moysklad.conversations import (
    append_message,
    enrich_clients,
    get_thread,
)
from plugins.moysklad.outreach import (
    build_outreach_for_row,
    facts_panel,
    generate_outreach_message,
    iter_generate_outreach_for_row_events,
    iter_personalize_batch_events,
    iter_rewrite_outreach_events,
    normalize_seller_fields,
    rewrite_outreach_message,
    sanity_check_outreach_message,
)

log = logging.getLogger(__name__)

router = APIRouter()

_SYNC_LOCK = threading.Lock()


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
    max_orders: int = 5000
    max_counterparties: int = 0
    include_archived: bool = False


class OutreachRewriteBody(BaseModel):
    message: str = ""
    channel: str = "telegram"
    client_id: str = ""
    seller_name: str = ""
    seller_facts: str = ""
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
    seller_name: str = ""
    seller_facts: str = ""
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


class MarkSentBody(BaseModel):
    message: str = ""
    channel: str = "telegram"
    client_id: str = ""
    open_deep_link: bool = True
    # When true (default for telegram), attempt Bot API send via Business bot.
    deliver: bool = True


class SellerSettingsBody(BaseModel):
    seller_name: str = ""
    seller_facts: str = ""
    # None = leave unchanged; "" = clear stored business connection id.
    telegram_business_connection_id: str | None = None


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


def _get_catalog(
    *,
    max_orders: int,
    max_counterparties: int,
    include_archived: bool,
    force: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(catalog, meta)`` where meta has sync/cache fields.

    Fresh durable cache is served without hitting MoySklad unless
    ``force=True`` (Синхронизация) or the TTL expired.
    """
    key = cache_key(
        max_orders=max_orders,
        max_counterparties=max_counterparties,
        include_archived=include_archived,
    )
    if not force:
        envelope = get_cached(key)
        if envelope is not None:
            synced_at = float(envelope.get("synced_at") or 0)
            return envelope["catalog"], {
                "cached": True,
                "synced_at": synced_at,
                "synced_at_label": format_synced_at(synced_at),
                "cache_ttl_seconds": int(
                    envelope.get("ttl_seconds") or cache_ttl_seconds()
                ),
                "cache_backend": cache_backend_name(),
            }

    with _SYNC_LOCK:
        # Another request may have filled the cache while we waited.
        if not force:
            envelope = get_cached(key)
            if envelope is not None:
                synced_at = float(envelope.get("synced_at") or 0)
                return envelope["catalog"], {
                    "cached": True,
                    "synced_at": synced_at,
                    "synced_at_label": format_synced_at(synced_at),
                    "cache_ttl_seconds": int(
                        envelope.get("ttl_seconds") or cache_ttl_seconds()
                    ),
                    "cache_backend": cache_backend_name(),
                }

        client = _client()
        catalog = build_enriched_catalog(
            client,
            max_orders=max_orders,
            max_counterparties=max_counterparties,
            include_archived=include_archived,
        )
        envelope = set_cached(key, catalog)
        synced_at = float(envelope.get("synced_at") or time.time())
        return catalog, {
            "cached": False,
            "synced_at": synced_at,
            "synced_at_label": format_synced_at(synced_at),
            "cache_ttl_seconds": int(
                envelope.get("ttl_seconds") or cache_ttl_seconds()
            ),
            "cache_backend": cache_backend_name(),
        }


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
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    max_orders: int = Query(5000, ge=0, le=100_000),
    max_counterparties: int = Query(0, ge=0, le=100_000),
    include_archived: bool = Query(False),
    refresh: bool = Query(False),
) -> dict[str, Any]:
    try:
        catalog, meta = _get_catalog(
            max_orders=max_orders,
            max_counterparties=max_counterparties,
            include_archived=include_archived,
            force=refresh,
        )
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
            limit=limit,
            offset=offset,
            max_orders=max_orders,
            max_counterparties=max_counterparties,
            include_archived=include_archived,
            catalog=catalog,
        )
        page["clients"] = enrich_clients(list(page.get("clients") or []))
        return _attach_cache_meta(_strip_internal(page), meta)
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /clients failed")
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
    """
    try:
        text = (body.message or "").strip()
        client_id = (body.client_id or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="message required")
        if not client_id:
            raise HTTPException(status_code=400, detail="client_id required")
        catalog, meta = _get_catalog(force=False)
        row = find_row_in_catalog(catalog, client_id)
        if row is None:
            raise HTTPException(status_code=404, detail="client not found in catalog")
        detail = build_client_detail(row)
        client = detail.get("client") or {}
        msg = detail.get("messaging") or {}
        channel = (body.channel or "telegram").strip().lower()
        deep_link = ""
        if channel == "whatsapp":
            deep_link = str(msg.get("whatsapp_url") or "")
        else:
            deep_link = str(msg.get("telegram_url") or "")

        delivery: dict[str, Any] = {"ok": False, "skipped": True}
        if body.deliver and channel.startswith("telegram"):
            delivery = send_outreach_to_client(
                text=text,
                tg_nick=str(client.get("tg_nick") or ""),
                tg_conversation=str(client.get("tg_conversation") or ""),
                tg_chat_id=str(
                    client.get("tg_chat_id")
                    or row.get("ТГ chat id")
                    or row.get("tg_chat_id")
                    or ""
                ),
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
            phone=str(client.get("phone") or ""),
            tg_nick=str(client.get("tg_nick") or ""),
            client_name=str(client.get("name") or ""),
            source=source,
        )
        open_link = bool(body.open_deep_link) and not delivery.get("ok")
        payload = {
            "ok": True,
            "conversation": thread,
            "facts": facts_panel({**detail, "conversation": thread}),
            "deep_link": deep_link if open_link else "",
            "channel": channel,
            "delivery": delivery,
            "telegram": telegram_send_status(),
        }
        return _attach_cache_meta(payload, meta)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /campaigns/mark-sent failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/clients/{client_id}/ai")
def post_client_ai(
    client_id: str,
    max_orders: int = Query(5000, ge=0, le=100_000),
    max_counterparties: int = Query(0, ge=0, le=100_000),
    include_archived: bool = Query(False),
) -> dict[str, Any]:
    """Generate AI summary + sales recommendation for a client card."""
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
        ai_block = generate_ai_for_detail(detail)
        return _attach_cache_meta(
            {
                "ok": True,
                "client_id": client_id,
                "ai": ai_block,
                "data_thin": detail.get("data_thin"),
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
    return {
        "ok": True,
        **get_seller_settings(),
        "telegram": telegram_send_status(),
    }


@router.put("/campaigns/seller-settings")
def put_campaign_seller_settings(body: SellerSettingsBody) -> dict[str, Any]:
    saved = save_seller_settings(
        seller_name=body.seller_name,
        seller_facts=body.seller_facts,
        telegram_business_connection_id=body.telegram_business_connection_id,
    )
    return {"ok": True, **saved, "telegram": telegram_send_status()}


@router.post("/campaigns/generate")
def post_campaign_generate(body: OutreachGenerateBody) -> dict[str, Any]:
    """AI (or heuristic) outreach text + facts panel for one client.

    Uses the same durable catalog cache as /clients (marketplace/direct).
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
        outreach = build_outreach_for_row(
            row,
            channel=body.channel,
            refresh_ai=bool(body.refresh_ai),
            seller_name=seller_name,
            seller_facts=seller_facts,
        )
        return _attach_cache_meta({"ok": True, **outreach}, meta)
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

    Tokens appear as soon as the model emits the ``message`` field (or plain
    text). Keeps the blocking ``/campaigns/generate`` for older clients.
    """
    try:
        client_id = (body.client_id or "").strip()
        if not client_id:
            raise HTTPException(status_code=400, detail="client_id required")
        catalog, _meta = _get_catalog(
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

        def _events() -> Iterator[dict[str, Any]]:
            try:
                yield from iter_generate_outreach_for_row_events(
                    row,
                    channel=body.channel,
                    refresh_ai=bool(body.refresh_ai),
                    seller_name=seller_name,
                    seller_facts=seller_facts,
                )
            except Exception as exc:  # pragma: no cover
                log.exception("moysklad /campaigns/generate/stream failed mid-stream")
                yield {"type": "error", "error": str(exc)}

        return _ndjson_response(_events())
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /campaigns/generate/stream failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
    try:
        draft = (body.message or "").strip()
        if not draft:
            raise HTTPException(status_code=400, detail="message required")
        seller_name, seller_facts = _resolve_seller(body.seller_name, body.seller_facts)
        if (body.seller_name or "").strip() or (body.seller_facts or "").strip():
            save_seller_settings(seller_name=seller_name, seller_facts=seller_facts)
        detail: dict[str, Any] | None = None
        client_id = (body.client_id or "").strip()
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

        def _events() -> Iterator[dict[str, Any]]:
            try:
                yield from iter_rewrite_outreach_events(
                    draft,
                    channel=body.channel,
                    seller_name=seller_name,
                    seller_facts=seller_facts,
                    detail=detail,
                )
            except Exception as exc:  # pragma: no cover
                log.exception("moysklad /campaigns/rewrite/stream failed mid-stream")
                yield {"type": "error", "error": str(exc)}

        return _ndjson_response(_events())
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /campaigns/rewrite/stream failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
