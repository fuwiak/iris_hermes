"""MoySklad Clients dashboard plugin — backend API.

Mounted at /api/plugins/moysklad/ by the dashboard plugin system.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

try:
    from fastapi import APIRouter, HTTPException, Query
    from pydantic import BaseModel, Field
except Exception:  # pragma: no cover — unit tests without fastapi
    class APIRouter:  # type: ignore[no-redef]
        def get(self, *_a, **_k):
            return lambda fn: fn

        def post(self, *_a, **_k):
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
    list_campaigns,
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
from plugins.moysklad.outreach import (
    build_outreach_for_row,
    facts_panel,
    generate_outreach_message,
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
    max_orders: int = 5000
    max_counterparties: int = 0
    include_archived: bool = False


class OutreachGenerateBody(BaseModel):
    client_id: str = ""
    channel: str = "telegram"
    refresh_ai: bool = True
    max_orders: int = 5000
    max_counterparties: int = 0
    include_archived: bool = False


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
        outreach = build_outreach_for_row(
            row,
            channel=body.channel,
            refresh_ai=bool(body.refresh_ai),
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
            want_ai = body.mode == "auto" or body.generate_ai or not (offer or "").strip()
            if want_ai and not (offer or "").strip():
                outreach = generate_outreach_message(
                    detail, channel=body.channel, refresh_ai=True
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
                tag_bit = (
                    f' по тегу «{body.group}»'
                    if (body.group or "").strip()
                    else ""
                )
                occasion = " к предстоящему поводу" if body.birthday_soon else ""
                offer = (
                    f"Здравствуйте! Это Iris, цветочный магазин{tag_bit}{occasion}. "
                    "Напишите, если удобно подобрать букет — без выдуманных скидок. "
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
