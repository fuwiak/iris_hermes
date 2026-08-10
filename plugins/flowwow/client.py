"""Flowwow marketplace HTTP client (sync).

Standalone plugin, same shape as ``plugins.moysklad.client`` — Bearer auth,
retry on 429/5xx, ``fetch_all`` pagination.

⚠️ Endpoint paths below (``/orders``, ``/shop/orders``, ``/clients``) are the
Flowwow seller-API conventions as documented for shop integrations; confirm
the exact paths and field names against the credentials/docs in your Flowwow
seller cabinet before relying on this in production — set
``FLOWWOW_API_URL`` to override if your account uses a different base or
version. ``flowwow_health`` is the fastest way to check a token actually
works against the configured base URL.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

DEFAULT_BASE = "https://api.flowwow.com/v1"
PAGE_SIZE = 100


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
                "(Flowwow seller cabinet → API / интеграции)."
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

    def get_page(
        self,
        path: str,
        *,
        limit: int = PAGE_SIZE,
        offset: int = 0,
        extra: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], int | None]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if extra:
            params.update(extra)
        if _delay_s():
            time.sleep(_delay_s())
        payload = self._request("GET", path, params=params)
        rows = payload.get("data") if isinstance(payload.get("data"), list) else payload.get("items")
        total = payload.get("total") or (payload.get("meta") or {}).get("total")
        return list(rows or []), total

    def fetch_all(
        self,
        path: str,
        *,
        max_rows: int = 0,
        extra: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        offset = 0
        unlimited = max_rows <= 0
        while unlimited or len(rows) < max_rows:
            batch_limit = PAGE_SIZE if unlimited else min(PAGE_SIZE, max_rows - len(rows))
            batch, _ = self.get_page(path, limit=batch_limit, offset=offset, extra=extra)
            if not batch:
                break
            for item in batch:
                rid = str((item or {}).get("id") or "").strip()
                if rid:
                    if rid in seen_ids:
                        continue
                    seen_ids.add(rid)
                rows.append(item)
                if not unlimited and len(rows) >= max_rows:
                    break
            if len(batch) < batch_limit:
                break
            offset += len(batch)
        return rows

    def health(self) -> dict[str, Any]:
        """One cheap call to confirm the token + base URL actually work."""
        rows, total = self.get_page("/orders", limit=1, offset=0)
        return {
            "ok": True,
            "base_url": self._base,
            "sample_order_id": str((rows[0] or {}).get("id")) if rows else None,
            "total": total,
        }

    def orders(
        self,
        *,
        fetch_all: bool = True,
        limit: int = 0,
        offset: int = 0,
        status: str | None = None,
    ) -> dict[str, Any]:
        extra = {"status": status} if status else None
        if fetch_all:
            rows = self.fetch_all("/orders", max_rows=limit, extra=extra)
            return {"rows": rows}
        rows, total = self.get_page("/orders", limit=limit or PAGE_SIZE, offset=offset, extra=extra)
        return {"rows": rows, "total": total}

    def clients(
        self,
        *,
        fetch_all: bool = True,
        limit: int = 0,
        offset: int = 0,
    ) -> dict[str, Any]:
        if fetch_all:
            rows = self.fetch_all("/clients", max_rows=limit)
            return {"rows": rows}
        rows, total = self.get_page("/clients", limit=limit or PAGE_SIZE, offset=offset)
        return {"rows": rows, "total": total}
