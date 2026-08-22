"""Marketplace cards feed for the «Карточки» dashboard tab.

Aggregates product cards from connected marketplaces (Flowwow live,
Yandex Market via Api-Key) into one payload. Read-only; the future
card-autopublish flow (call 21.08.2026) builds on top of this.

Caching: an in-process dict for the hot path plus the durable envelope
layer of ``catalog_cache`` (memory → Elasticsearch → Redis → file), so
a container restart or a second worker does not refetch both
marketplaces. ``force=True`` bypasses every layer.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"ts": 0.0, "payload": None, "key": ""}

_CACHE_KEY_VERSION = "v1"


def _cache_ttl_s() -> float:
    raw = (os.environ.get("MOYSKLAD_MARKETPLACE_CARDS_TTL_S") or "").strip()
    try:
        return max(60.0, float(raw)) if raw else 900.0
    except ValueError:
        return 900.0


def _durable_key(limit: int) -> str:
    return f"moysklad:marketplace:cards:{_CACHE_KEY_VERSION}:l{int(limit)}"


def _durable_get(key: str) -> dict[str, Any] | None:
    try:
        from plugins.moysklad.catalog_cache import get_raw_envelope

        envelope = get_raw_envelope(key, fresh=True)
        payload = (envelope or {}).get("payload")
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _durable_set(key: str, payload: dict[str, Any]) -> None:
    try:
        from plugins.moysklad.catalog_cache import set_raw_envelope

        set_raw_envelope(
            key,
            {"payload": payload, "ttl_seconds": _cache_ttl_s()},
            kind="marketplace_cards",
        )
    except Exception:
        pass


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


_NAME_NOISE_RE = None


def _normalize_card_name(name: str) -> str:
    """Fold a card title for cross-marketplace matching."""
    global _NAME_NOISE_RE
    import re

    if _NAME_NOISE_RE is None:
        _NAME_NOISE_RE = re.compile(r"[^0-9a-zа-яё]+", re.IGNORECASE)
    return " ".join(_NAME_NOISE_RE.sub(" ", (name or "").lower()).split())


def _card_status(product: dict[str, Any]) -> str:
    if product.get("is_archived"):
        return "archived"
    return "active" if product.get("is_active") else "hidden"


def build_combined_cards(
    flowwow: dict[str, Any],
    yandex: dict[str, Any],
) -> list[dict[str, Any]]:
    """One row per card; a card present on both marketplaces carries both.

    Matching key: normalized name (lowercase, punctuation/emoji stripped).
    Per-marketplace details stay under ``listings[marketplace]``.
    """
    combined: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    def _add(marketplace: str, product: dict[str, Any]) -> None:
        key = _normalize_card_name(str(product.get("name") or ""))
        if not key:
            key = f"{marketplace}:{product.get('offer_id') or product.get('product_id')}"
        row = combined.get(key)
        if row is None:
            row = {
                "name": product.get("name") or "",
                "image": product.get("image") or "",
                "marketplaces": [],
                "statuses": [],
                "listings": {},
            }
            combined[key] = row
            order.append(key)
        if not row["image"] and product.get("image"):
            row["image"] = product["image"]
        if marketplace not in row["marketplaces"]:
            row["marketplaces"].append(marketplace)
        status = _card_status(product)
        if status not in row["statuses"]:
            row["statuses"].append(status)
        row["listings"][marketplace] = product

    for product in flowwow.get("products") or []:
        _add("flowwow", product)
    for product in yandex.get("products") or []:
        _add("yandex_market", product)
    return [combined[key] for key in order]


def cached_payload() -> dict[str, Any] | None:
    """Last built payload without triggering marketplace calls."""
    with _cache_lock:
        payload = _cache.get("payload")
        key = str(_cache.get("key") or "")
    if isinstance(payload, dict):
        return payload
    return _durable_get(key) if key else None


def marketplace_cards_payload(*, limit: int = 100, force: bool = False) -> dict[str, Any]:
    """Cards from all marketplaces, cached in-process + Elasticsearch/Redis/file."""
    now = time.time()
    key = _durable_key(limit)
    if not force:
        with _cache_lock:
            cached = _cache.get("payload")
            hit = (
                cached is not None
                and _cache.get("key") == key
                and now - float(_cache["ts"]) < _cache_ttl_s()
            )
        if hit:
            return cached
        durable = _durable_get(key)
        if durable is not None:
            with _cache_lock:
                _cache["ts"] = now
                _cache["payload"] = durable
                _cache["key"] = key
            return durable
    flowwow = _flowwow_section(limit)
    yandex = _yandex_section(limit)
    payload = {
        "flowwow": flowwow,
        "yandex": yandex,
        "combined": build_combined_cards(flowwow, yandex),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with _cache_lock:
        _cache["ts"] = now
        _cache["payload"] = payload
        _cache["key"] = key
    _durable_set(key, payload)
    return payload
