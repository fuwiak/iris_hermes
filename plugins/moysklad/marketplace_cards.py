"""Marketplace cards feed for the «Карточки» dashboard tab.

Aggregates product cards from connected marketplaces (Flowwow live,
Yandex Market pending token) into one payload. Read-only; the future
card-autopublish flow (call 21.08.2026) builds on top of this.
"""

from __future__ import annotations

import threading
import time
from typing import Any

_CACHE_TTL_S = 300.0
_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"ts": 0.0, "payload": None}


def _slim_product(row: dict[str, Any]) -> dict[str, Any]:
    images = row.get("images") or []
    description = str(row.get("description") or "")
    return {
        "product_id": row.get("productId"),
        "offer_id": row.get("offerId") or "",
        "name": row.get("name") or "",
        "description_preview": description[:200],
        "description": description[:4000],
        "price": row.get("price"),
        "discount": row.get("discount"),
        "currency": row.get("currencyCode") or "RUB",
        "is_active": bool(row.get("isActive")),
        "is_archived": bool(row.get("isArchived")),
        "url": row.get("url") or "",
        "image": images[0] if images else "",
        "images_count": len(images),
    }


def _flowwow_section(limit: int) -> dict[str, Any]:
    try:
        from plugins.flowwow.client import FlowwowClient, FlowwowError, token_configured
    except Exception as exc:  # pragma: no cover — plugin missing
        return {"configured": False, "error": f"flowwow plugin unavailable: {exc}"}
    if not token_configured():
        return {
            "configured": False,
            "note": "FLOWWOW_API_TOKEN не задан — карточки Flowwow недоступны.",
        }
    try:
        client = FlowwowClient()
        shops = client.shops(status="active")
        shop_rows = list(shops.get("rows") or [])
        if not shop_rows:
            return {"configured": True, "shops": [], "products": [], "total": 0}
        shop = shop_rows[0]
        shop_id = int(shop.get("shopId") or 0)
        products = client.products(shop_id, limit=limit)
        return {
            "configured": True,
            "shop": {
                "shop_id": shop_id,
                "name": shop.get("name") or "",
                "address": shop.get("address") or "",
            },
            "shops_total": shops.get("total"),
            "products": [_slim_product(r) for r in products.get("rows") or []],
            "total": products.get("total"),
        }
    except FlowwowError as exc:
        return {"configured": True, "error": str(exc)}
    except Exception as exc:  # pragma: no cover — defensive
        return {"configured": True, "error": f"{type(exc).__name__}: {exc}"}


def _yandex_section(limit: int) -> dict[str, Any]:
    try:
        from plugins.moysklad.yandex_market import (
            YandexMarketError,
            fetch_yandex_cards,
            token_configured,
        )
    except Exception as exc:  # pragma: no cover — module missing
        return {"configured": False, "error": f"yandex_market unavailable: {exc}"}
    if not token_configured():
        return {
            "configured": False,
            "note": (
                "Нет API-токена Яндекс Маркета. Нужен Api-Key из кабинета "
                "продавца (Настройки → API) — добавить в .env как "
                "YANDEX_MARKET_API_TOKEN."
            ),
        }
    try:
        data = fetch_yandex_cards(limit=limit)
        return {"configured": True, **data}
    except YandexMarketError as exc:
        return {"configured": True, "error": str(exc)}
    except Exception as exc:  # pragma: no cover — defensive
        return {"configured": True, "error": f"{type(exc).__name__}: {exc}"}


def cached_payload() -> dict[str, Any] | None:
    """Last built payload without triggering marketplace calls."""
    with _cache_lock:
        payload = _cache.get("payload")
    return payload if isinstance(payload, dict) else None


def marketplace_cards_payload(*, limit: int = 100, force: bool = False) -> dict[str, Any]:
    """Cards from all marketplaces, cached for 5 minutes."""
    now = time.time()
    with _cache_lock:
        cached = _cache.get("payload")
        if not force and cached is not None and now - float(_cache["ts"]) < _CACHE_TTL_S:
            return cached
    payload = {
        "flowwow": _flowwow_section(limit),
        "yandex": _yandex_section(limit),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with _cache_lock:
        _cache["ts"] = now
        _cache["payload"] = payload
    return payload
