"""MoySklad Remap 1.2 HTTP client (sync).

Ported from client_segmentation_deepseek/app/services/moysklad/client.py —
Bearer auth, pagination, counterparties / orders / positions / channels / tags.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

DEFAULT_BASE = "https://api.moysklad.ru/api/remap/1.2"
PAGE_SIZE = 1000


class MoySkladError(RuntimeError):
    """API or config failure surfaced to tool handlers."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _token_from_railway_cli() -> str:
    """Local-dev fallback: pull token via Railway CLI when unset in env/.env."""
    if os.environ.get("HERMES_CONTAINER") or os.environ.get("CI"):
        return ""
    try:
        import json
        import shutil
        import subprocess

        if not shutil.which("railway"):
            return ""
        proc = subprocess.run(
            ["railway", "variable", "list", "--json"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if proc.returncode != 0:
            return ""
        data = json.loads(proc.stdout)
        if not isinstance(data, dict):
            return ""
        return str(data.get("MOYSKLAD_API_TOKEN") or "").strip()
    except Exception:
        return ""


def _token() -> str:
    token = (os.environ.get("MOYSKLAD_API_TOKEN") or "").strip()
    if token:
        return token
    token = _token_from_railway_cli()
    if token:
        # Keep the rest of the process (and child tools) consistent.
        os.environ["MOYSKLAD_API_TOKEN"] = token
    return token


def token_configured() -> bool:
    return bool(_token())


def _base_url() -> str:
    return (os.environ.get("MOYSKLAD_API_URL") or DEFAULT_BASE).rstrip("/")


def _delay_s() -> float:
    try:
        return max(0.0, float(os.environ.get("MOYSKLAD_REQUEST_DELAY_MS", "250")) / 1000.0)
    except ValueError:
        return 0.25


def _retry_max() -> int:
    try:
        return max(0, int(os.environ.get("MOYSKLAD_API_RETRY_MAX", "4")))
    except ValueError:
        return 4


class MoySkladClient:
    def __init__(self) -> None:
        token = _token()
        if not token:
            raise MoySkladError(
                "MOYSKLAD_API_TOKEN missing. Add it to ~/.hermes/.env "
                "(Iris enables the moysklad plugin by default)."
            )
        self._token = token
        self._base = _base_url()

    def _headers(self) -> dict[str, str]:
        # Remap 1.2 rejects bare Accept: application/json (error 1062) —
        # requires charset=utf-8. Matches MoySklad docs / live API.
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json;charset=utf-8",
            "Accept-Encoding": "gzip",
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
                    last_exc = MoySkladError(
                        f"HTTP {resp.status_code}", status_code=resp.status_code
                    )
                    continue
                if resp.status_code >= 400:
                    raise MoySkladError(
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
                raise MoySkladError(f"Request failed {method} {path}: {exc}") from exc
        raise MoySkladError(f"Request failed after retries: {last_exc}")

    def get_page(
        self,
        path: str,
        *,
        limit: int = 50,
        offset: int = 0,
        extra: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], int | None]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if extra:
            params.update(extra)
        if _delay_s():
            time.sleep(_delay_s())
        payload = self._request("GET", path, params=params)
        total = (payload.get("meta") or {}).get("size")
        return list(payload.get("rows") or []), total

    def fetch_all(
        self,
        path: str,
        *,
        max_rows: int = 0,
        extra: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        unlimited = max_rows <= 0
        while unlimited or len(rows) < max_rows:
            batch_limit = PAGE_SIZE if unlimited else min(PAGE_SIZE, max_rows - len(rows))
            batch, _ = self.get_page(path, limit=batch_limit, offset=offset, extra=extra)
            if not batch:
                break
            rows.extend(batch)
            if len(batch) < batch_limit:
                break
            offset += len(batch)
        return rows

    def health(self) -> dict[str, Any]:
        rows, total = self.get_page("/entity/counterparty", limit=1, offset=0)
        return {"ok": True, "sample_rows": len(rows), "total": total}

    def counterparties(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        search: str = "",
        fetch_all: bool = False,
        include_archived: bool = False,
    ) -> dict[str, Any]:
        extra: dict[str, Any] = {"expand": "state"}
        filters: list[str] = []
        if not include_archived:
            filters.append("archived=false")
        if search:
            filters.append(f"name~{search}")
        if filters:
            extra["filter"] = ";".join(filters)
        if fetch_all:
            rows = self.fetch_all("/entity/counterparty", max_rows=limit or 0, extra=extra)
            return {"total": len(rows), "count": len(rows), "rows": rows}
        rows, total = self.get_page(
            "/entity/counterparty", limit=limit, offset=offset, extra=extra
        )
        return {"total": total, "count": len(rows), "rows": rows}

    def orders(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        agent_id: str = "",
        fetch_all: bool = False,
    ) -> dict[str, Any]:
        extra: dict[str, Any] = {"expand": "agent,state,salesChannel"}
        if agent_id:
            href = f"{self._base}/entity/counterparty/{agent_id}"
            extra["filter"] = f"agent={href}"
        if fetch_all:
            rows = self.fetch_all("/entity/customerorder", max_rows=limit or 0, extra=extra)
            return {"total": len(rows), "count": len(rows), "rows": rows}
        rows, total = self.get_page(
            "/entity/customerorder", limit=limit, offset=offset, extra=extra
        )
        return {"total": total, "count": len(rows), "rows": rows}

    def positions(self, order_id: str) -> dict[str, Any]:
        rows, total = self.get_page(
            f"/entity/customerorder/{order_id}/positions",
            limit=1000,
            offset=0,
            extra={"expand": "assortment"},
        )
        return {
            "order_id": order_id,
            "total": total,
            "count": len(rows),
            "rows": rows,
        }

    def channels(
        self, *, limit: int = 100, offset: int = 0, fetch_all: bool = False
    ) -> dict[str, Any]:
        if fetch_all:
            rows = self.fetch_all("/entity/saleschannel", max_rows=limit or 0)
            return {"total": len(rows), "count": len(rows), "rows": rows}
        rows, total = self.get_page(
            "/entity/saleschannel", limit=limit, offset=offset
        )
        return {"total": total, "count": len(rows), "rows": rows}

    def push_tags(self, counterparty_id: str, tags: list[str]) -> dict[str, Any]:
        result = self._request(
            "PUT",
            f"/entity/counterparty/{counterparty_id}",
            json_body={"tags": tags},
        )
        return {
            "ok": True,
            "counterparty_id": counterparty_id,
            "tags": tags,
            "name": result.get("name"),
            "id": result.get("id"),
        }
