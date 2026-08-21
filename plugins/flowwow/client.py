"""Flowwow seller HTTP client (sync).

Verified live against the official «Открытое API для продавцов (0.0.1)»
(https://seller-docs.flowwow.com → 5.1 Документация и поддержка по API):

- Base URL: ``https://apis.flowwow.com`` (override: ``FLOWWOW_API_URL``)
- Auth: ``Authorization: Bearer <FLOWWOW_API_TOKEN>``
- Everything except ping is ``POST`` with a JSON body; product/stock/price
  endpoints additionally take ``?shopId=<int>`` as a query parameter.

Endpoints exposed here (read-only):

- ``GET  /apiseller/ping/check``           — health, no auth
- ``POST /apiseller/shops``                — shops list (paged, limit ≤ 50)
- ``POST /apiseller/products``             — products of one shop (limit ≤ 1000)
- ``POST /apiseller/stocks/get``           — stock per offerId
- ``POST /apiseller/prices/get``           — price/discount per offerId

The open seller API has NO orders/clients endpoints as of 0.0.1 — orders
flow must go through a different channel (cabinet/webhooks) for now.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

DEFAULT_BASE = "https://apis.flowwow.com"
SHOPS_PAGE_SIZE = 50  # hard API max for /apiseller/shops
PRODUCTS_PAGE_SIZE = 100  # API allows up to 1000; keep responses chat-sized


class FlowwowError(RuntimeError):
    """API or config failure surfaced to tool handlers."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _token() -> str:
    return (os.environ.get("FLOWWOW_API_TOKEN") or "").strip()


def token_configured() -> bool:
    return bool(_token())


def _base_url() -> str:
    return (os.environ.get("FLOWWOW_API_URL") or DEFAULT_BASE).rstrip("/")


def _delay_s() -> float:
    raw = (os.environ.get("FLOWWOW_REQUEST_DELAY_MS") or "").strip()
    try:
        return max(0.0, float(raw)) / 1000.0 if raw else 0.25
    except ValueError:
        return 0.25


def _retry_max() -> int:
    raw = (os.environ.get("FLOWWOW_API_RETRY_MAX") or "").strip()
    try:
        return max(0, int(raw)) if raw else 4
    except ValueError:
        return 4


class FlowwowClient:
    def __init__(self) -> None:
        token = _token()
        if not token:
            raise FlowwowError(
                "FLOWWOW_API_TOKEN missing. Add it to ~/.hermes/.env "
                "(issued by Flowwow support / seller cabinet)."
            )
        self._token = token
        self._base = _base_url()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }

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
        retries = _retry_max()
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            if attempt and _delay_s():
                time.sleep(_delay_s() * (attempt + 1))
            try:
                with httpx.Client(timeout=timeout) as client:
                    resp = client.request(
                        method,
                        url,
                        headers=self._headers(),
                        params=params,
                        json=json_body,
                    )
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                    last_exc = FlowwowError(
                        f"HTTP {resp.status_code}", status_code=resp.status_code
                    )
                    continue
                if resp.status_code >= 400:
                    raise FlowwowError(
                        f"HTTP {resp.status_code} {method} {path}: {resp.text[:500]}",
                        status_code=resp.status_code,
                    )
                if not resp.content:
                    return {}
                return resp.json()
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < retries:
                    continue
                raise FlowwowError(f"Request failed {method} {path}: {exc}") from exc
        raise FlowwowError(f"Request failed after retries: {last_exc}")

    def _post(
        self,
        path: str,
        body: dict[str, Any],
        *,
        shop_id: int | None = None,
    ) -> dict[str, Any]:
        if _delay_s():
            time.sleep(_delay_s())
        params = {"shopId": shop_id} if shop_id else None
        return self._request("POST", path, params=params, json_body=body)

    def ping(self) -> dict[str, Any]:
        return self._request("GET", "/apiseller/ping/check", params={"say": "hello"})

    def health(self) -> dict[str, Any]:
        """Ping (no auth) + one shops page to confirm the token works."""
        ping = self.ping()
        shops = self._post("/apiseller/shops", {"status": "active", "limit": 1})
        rows = list(shops.get("shops") or [])
        return {
            "ok": bool(ping.get("say") == "hello" and rows),
            "base_url": self._base,
            "ping": ping.get("say"),
            "active_shops": shops.get("total"),
            "sample_shop": (
                {"shopId": rows[0].get("shopId"), "name": rows[0].get("name")}
                if rows
                else None
            ),
        }

    def shops(self, *, status: str = "active") -> dict[str, Any]:
        """All shops with the given status (moderation | active | disabled)."""
        rows: list[dict[str, Any]] = []
        page = 0
        total: int | None = None
        while True:
            payload = self._post(
                "/apiseller/shops",
                {"status": status, "page": page, "limit": SHOPS_PAGE_SIZE},
            )
            batch = list(payload.get("shops") or [])
            total = payload.get("total", total)
            rows.extend(batch)
            if len(batch) < SHOPS_PAGE_SIZE or (total is not None and len(rows) >= total):
                break
            page += 1
        return {"rows": rows, "total": total if total is not None else len(rows)}

    def products(
        self,
        shop_id: int,
        *,
        limit: int = 0,
        with_archive: bool = False,
        extended: bool = False,
    ) -> dict[str, Any]:
        """Products of one shop. ``limit`` 0 = all pages."""
        rows: list[dict[str, Any]] = []
        page = 0
        total: int | None = None
        unlimited = limit <= 0
        while True:
            page_size = (
                PRODUCTS_PAGE_SIZE
                if unlimited
                else min(PRODUCTS_PAGE_SIZE, limit - len(rows))
            )
            payload = self._post(
                "/apiseller/products",
                {
                    "page": page,
                    "limit": page_size,
                    "withArchive": with_archive,
                    "extended": extended,
                },
                shop_id=shop_id,
            )
            batch = list(payload.get("items") or [])
            total = payload.get("total", total)
            rows.extend(batch)
            if not unlimited and len(rows) >= limit:
                rows = rows[:limit]
                break
            if len(batch) < page_size:
                break
            page += 1
        return {"rows": rows, "total": total if total is not None else len(rows)}

    def stocks(self, shop_id: int, offer_ids: list[str]) -> dict[str, Any]:
        """Stock levels for the given offerIds (seller-side product ids)."""
        return self._post(
            "/apiseller/stocks/get",
            {"offers": [{"offerId": oid} for oid in offer_ids]},
            shop_id=shop_id,
        )

    def prices(self, shop_id: int, offer_ids: list[str]) -> dict[str, Any]:
        """Prices/discounts for the given offerIds."""
        return self._post(
            "/apiseller/prices/get",
            {"offers": [{"offerId": oid} for oid in offer_ids]},
            shop_id=shop_id,
        )
