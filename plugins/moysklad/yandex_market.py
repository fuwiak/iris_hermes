"""Yandex Market partner API client (read-only) for the «Карточки» tab.

Verified live 21.08.2026 against https://api.partner.market.yandex.ru
(docs: https://yandex.ru/dev/market/partner-api/doc/ru/):

- Auth: ``Api-Key: <YANDEX_MARKET_API_TOKEN>`` header (ACMA:… key)
- ``GET  /campaigns`` — placements; each carries ``business.id``
- ``POST /businesses/{id}/offer-mappings?limit=N&page_token=…`` — product
  cards: name, description (HTML), pictures, basicPrice, cardStatus,
  mapping.marketModelId/marketSku
- ``POST /businesses/{id}/offer-cards`` — per-offer contentRating (0–100)
"""

from __future__ import annotations

import os
import re
import time
from typing import Any

import httpx

DEFAULT_BASE = "https://api.partner.market.yandex.ru"
PAGE_SIZE = 100  # offer-mappings hard max is 200

_TAG_RE = re.compile(r"<[^>]+>")


class YandexMarketError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _token() -> str:
    return (
        os.environ.get("YANDEX_MARKET_API_TOKEN")
        or os.environ.get("YANDEX_MARKET_TOKEN")
        or ""
    ).strip()


def token_configured() -> bool:
    return bool(_token())


def _base_url() -> str:
    return (os.environ.get("YANDEX_MARKET_API_URL") or DEFAULT_BASE).rstrip("/")


def strip_html(text: str) -> str:
    return _TAG_RE.sub(" ", text or "").replace("&nbsp;", " ").strip()


class YandexMarketClient:
    def __init__(self) -> None:
        token = _token()
        if not token:
            raise YandexMarketError(
                "YANDEX_MARKET_API_TOKEN missing. Api-Key from the seller "
                "cabinet (Настройки → API) goes into ~/.hermes/.env."
            )
        self._token = token
        self._base = _base_url()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        url = f"{self._base}{path}"
        last_exc: Exception | None = None
        for attempt in range(4):
            if attempt:
                time.sleep(0.5 * attempt)
            try:
                with httpx.Client(timeout=timeout) as client:
                    resp = client.request(
                        method,
                        url,
                        headers={
                            "Api-Key": self._token,
                            "Accept": "application/json",
                        },
                        params=params,
                        json=json_body,
                    )
                if resp.status_code in (420, 429, 500, 502, 503, 504) and attempt < 3:
                    last_exc = YandexMarketError(
                        f"HTTP {resp.status_code}", status_code=resp.status_code
                    )
                    continue
                if resp.status_code >= 400:
                    raise YandexMarketError(
                        f"HTTP {resp.status_code} {method} {path}: {resp.text[:500]}",
                        status_code=resp.status_code,
                    )
                return resp.json() if resp.content else {}
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < 3:
                    continue
                raise YandexMarketError(
                    f"Request failed {method} {path}: {exc}"
                ) from exc
        raise YandexMarketError(f"Request failed after retries: {last_exc}")

    def campaigns(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/campaigns")
        return list(payload.get("campaigns") or [])

    def business(self) -> dict[str, Any]:
        """First business behind the key: {'id': …, 'name': …}."""
        for campaign in self.campaigns():
            business = campaign.get("business") or {}
            if business.get("id"):
                return business
        raise YandexMarketError("No business behind this Api-Key (no campaigns).")

    def offer_mappings(
        self, business_id: int, *, limit: int = 0
    ) -> list[dict[str, Any]]:
        """Product cards of a business; ``limit`` 0 = all pages."""
        rows: list[dict[str, Any]] = []
        page_token = ""
        unlimited = limit <= 0
        while True:
            page_size = PAGE_SIZE if unlimited else min(PAGE_SIZE, limit - len(rows))
            params: dict[str, Any] = {"limit": page_size}
            if page_token:
                params["page_token"] = page_token
            payload = self._request(
                "POST",
                f"/businesses/{business_id}/offer-mappings",
                params=params,
                json_body={},
            )
            result = payload.get("result") or {}
            batch = list(result.get("offerMappings") or [])
            rows.extend(batch)
            page_token = str((result.get("paging") or {}).get("nextPageToken") or "")
            if (not unlimited and len(rows) >= limit) or not page_token or not batch:
                break
        return rows if unlimited else rows[:limit]

    def content_ratings(
        self, business_id: int, offer_ids: list[str]
    ) -> dict[str, int]:
        """offerId → contentRating (0–100) via /offer-cards."""
        ratings: dict[str, int] = {}
        for start in range(0, len(offer_ids), PAGE_SIZE):
            chunk = offer_ids[start : start + PAGE_SIZE]
            payload = self._request(
                "POST",
                f"/businesses/{business_id}/offer-cards",
                params={"limit": len(chunk)},
                json_body={"offerIds": chunk},
            )
            for card in (payload.get("result") or {}).get("offerCards") or []:
                oid = str(card.get("offerId") or "")
                rating = card.get("contentRating")
                if oid and rating is not None:
                    ratings[oid] = int(rating)
        return ratings


def slim_card(mapping_row: dict[str, Any], ratings: dict[str, int]) -> dict[str, Any]:
    """offer-mappings row → the shared marketplace-card shape."""
    offer = mapping_row.get("offer") or {}
    mapping = mapping_row.get("mapping") or {}
    pictures = offer.get("pictures") or []
    price = (offer.get("basicPrice") or {}).get("value")
    currency = str((offer.get("basicPrice") or {}).get("currencyId") or "RUR")
    model_id = mapping.get("marketModelId")
    sku = mapping.get("marketSku")
    url = ""
    if model_id:
        url = f"https://market.yandex.ru/product/{model_id}"
        if sku:
            url += f"?sku={sku}"
    offer_id = str(offer.get("offerId") or "")
    card_status = str(offer.get("cardStatus") or "")
    description = strip_html(str(offer.get("description") or ""))
    return {
        "product_id": sku,
        "offer_id": offer_id,
        "name": offer.get("name") or "",
        "description_preview": description[:200],
        "description": description[:4000],
        "price": str(price) if price is not None else None,
        "discount": None,
        "currency": "RUB" if currency in ("RUR", "RUB") else currency,
        "is_active": card_status.startswith("HAS_CARD"),
        "is_archived": bool(offer.get("archived")),
        "url": url,
        "image": pictures[0] if pictures else "",
        "images_count": len(pictures),
        "card_status": card_status,
        "content_rating": ratings.get(offer_id),
    }


def fetch_yandex_cards(*, limit: int = 100) -> dict[str, Any]:
    """Business + slimmed cards, ready for the «Карточки» payload."""
    client = YandexMarketClient()
    business = client.business()
    business_id = int(business["id"])
    rows = client.offer_mappings(business_id, limit=limit)
    offer_ids = [
        str((r.get("offer") or {}).get("offerId") or "") for r in rows
    ]
    ratings = client.content_ratings(business_id, [o for o in offer_ids if o])
    return {
        "business": {"id": business_id, "name": business.get("name") or ""},
        "products": [slim_card(r, ratings) for r in rows],
        "total": len(rows),
    }
