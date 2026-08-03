"""MoySklad Clients dashboard plugin — backend API.

Mounted at /api/plugins/moysklad/ by the dashboard plugin system.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

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
from plugins.moysklad.campaigns import (
    create_draft,
    delete_campaign,
    list_campaigns,
)
from plugins.moysklad.classify import build_enriched_catalog, clients_page
from plugins.moysklad.client import MoySkladClient, MoySkladError, token_configured

log = logging.getLogger(__name__)

router = APIRouter()

CACHE_TTL_SECONDS = 60
_CACHE_LOCK = threading.Lock()
_CATALOG_CACHE: Optional[dict[str, Any]] = None
_CATALOG_CACHE_AT = 0.0
_CATALOG_CACHE_KEY: tuple[Any, ...] = ()


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


class CampaignCreateBody(BaseModel):
    title: str = "Рассылка"
    channel: str = "telegram"
    mode: str = "manual"
    offer: str = ""
    sales_filter: str = "all"
    group: str = ""
    q: str = ""
    include_preview: bool = True
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
) -> dict[str, Any]:
    global _CATALOG_CACHE, _CATALOG_CACHE_AT, _CATALOG_CACHE_KEY
    key = (max_orders, max_counterparties, include_archived)
    now = time.time()
    with _CACHE_LOCK:
        if (
            not force
            and _CATALOG_CACHE is not None
            and _CATALOG_CACHE_KEY == key
            and (now - _CATALOG_CACHE_AT) < CACHE_TTL_SECONDS
        ):
            return _CATALOG_CACHE
    client = _client()
    catalog = build_enriched_catalog(
        client,
        max_orders=max_orders,
        max_counterparties=max_counterparties,
        include_archived=include_archived,
    )
    with _CACHE_LOCK:
        _CATALOG_CACHE = catalog
        _CATALOG_CACHE_AT = time.time()
        _CATALOG_CACHE_KEY = key
    return catalog


def _invalidate_cache() -> None:
    global _CATALOG_CACHE, _CATALOG_CACHE_AT
    with _CACHE_LOCK:
        _CATALOG_CACHE = None
        _CATALOG_CACHE_AT = 0.0


def _strip_internal(page: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in page.items() if not k.startswith("_")}


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
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    max_orders: int = Query(5000, ge=0, le=100_000),
    max_counterparties: int = Query(0, ge=0, le=100_000),
    include_archived: bool = Query(False),
    refresh: bool = Query(False),
) -> dict[str, Any]:
    try:
        catalog = _get_catalog(
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
            limit=limit,
            offset=offset,
            max_orders=max_orders,
            max_counterparties=max_counterparties,
            include_archived=include_archived,
            catalog=catalog,
        )
        out = _strip_internal(page)
        out["cached"] = not refresh
        out["cache_ttl_seconds"] = CACHE_TTL_SECONDS
        return out
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /clients failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/groups/assign")
def post_groups_assign(body: AssignBody) -> dict[str, Any]:
    try:
        catalog = _get_catalog(
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
                _invalidate_cache()
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


@router.get("/campaigns")
def get_campaigns() -> dict[str, Any]:
    return {"ok": True, "campaigns": list_campaigns()}


@router.post("/campaigns")
def post_campaign(body: CampaignCreateBody) -> dict[str, Any]:
    try:
        catalog = _get_catalog(
            max_orders=body.max_orders,
            max_counterparties=body.max_counterparties,
            include_archived=body.include_archived,
        )
        page = clients_page(
            _client(),
            sales_filter=body.sales_filter,
            group=body.group,
            q=body.q,
            limit=20 if body.include_preview else 1,
            offset=0,
            catalog=catalog,
        )
        preview = []
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
        offer = body.offer
        if body.mode == "auto" and not (offer or "").strip():
            offer = (
                "Здравствуйте! Специально для вас — персональное предложение "
                "от Iris. Напишите, если удобно продолжить диалог."
            )
        item = create_draft(
            title=body.title,
            channel=body.channel,
            mode=body.mode,
            offer=offer,
            sales_filter=page.get("sales_filter") or body.sales_filter,
            group=body.group,
            q=body.q,
            audience_count=int(page.get("matched_total") or 0),
            audience_preview=preview,
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
