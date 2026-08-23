"""Real Yandex Market sales from the partner API for dashboard reconciliation.

Why: MoySklad orders store pre-discount (list) prices, so the yandex_market
channel computed from MoySklad runs ~1.5–2× above what buyers actually paid
(cabinet numbers). ``POST /campaigns/{id}/stats/orders`` returns the truth:
per item BUYER total (what the buyer paid) and MARKETPLACE total (what the
seller is paid out). The dashboard shows both next to the MoySklad-derived
figure instead of silently disagreeing with the client's report.

Cached through the durable envelope layer (memory → ES → Redis → file).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

_CANCEL_PREFIXES = ("CANCELLED", "RETURN", "UNPAID")
_CACHE_TTL_S = 1800.0
_CACHE_KEY = "moysklad:yandex:stats:v1"


def _months_back(today: date, months: int) -> str:
    year, month = today.year, today.month
    for _ in range(max(0, months)):
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return f"{year:04d}-{month:02d}-01"


def fetch_yandex_monthly_stats(*, months: int = 3, today: date | None = None) -> dict[str, Any]:
    """{'months': {'2026-07': {orders, buyer_total, payout_total}}, 'source': …}."""
    from plugins.moysklad.yandex_market import YandexMarketClient

    today = today or date.today()
    date_from = _months_back(today, months)
    date_to = (today + timedelta(days=1)).isoformat()
    client = YandexMarketClient()
    campaigns = client.campaigns()
    agg: dict[str, dict[str, float]] = {}
    for campaign in campaigns:
        campaign_id = campaign.get("id")
        if not campaign_id:
            continue
        page_token = ""
        while True:
            params: dict[str, Any] = {"limit": 200}
            if page_token:
                params["page_token"] = page_token
            payload = client._request(
                "POST",
                f"/campaigns/{campaign_id}/stats/orders",
                params=params,
                json_body={"dateFrom": date_from, "dateTo": date_to},
            )
            result = payload.get("result") or {}
            for order in result.get("orders") or []:
                status = str(order.get("status") or "")
                if status.startswith(_CANCEL_PREFIXES):
                    continue
                month_id = str(order.get("creationDate") or "")[:7]
                if len(month_id) != 7:
                    continue
                cell = agg.setdefault(
                    month_id, {"orders": 0, "buyer_total": 0.0, "payout_total": 0.0}
                )
                cell["orders"] += 1
                for item in order.get("items") or []:
                    for price in item.get("prices") or []:
                        total = float(price.get("total") or 0)
                        if price.get("type") == "BUYER":
                            cell["buyer_total"] += total
                        elif price.get("type") == "MARKETPLACE":
                            cell["payout_total"] += total
            page_token = str((result.get("paging") or {}).get("nextPageToken") or "")
            if not page_token:
                break
    for cell in agg.values():
        cell["buyer_total"] = round(cell["buyer_total"], 2)
        cell["payout_total"] = round(cell["payout_total"], 2)
    return {
        "months": dict(sorted(agg.items())),
        "campaigns": len(campaigns),
        "date_from": date_from,
        "date_to": date_to,
    }


def yandex_monthly_stats_cached(*, months: int = 3, force: bool = False) -> dict[str, Any] | None:
    """Durable-cached stats; ``None`` when the token is missing or API fails."""
    from plugins.moysklad.yandex_market import token_configured

    if not token_configured():
        return None
    key = f"{_CACHE_KEY}:m{int(months)}"
    if not force:
        try:
            from plugins.moysklad.catalog_cache import get_raw_envelope

            envelope = get_raw_envelope(key, fresh=True)
            stats = (envelope or {}).get("payload")
            if isinstance(stats, dict):
                return stats
        except Exception:
            pass
    try:
        stats = fetch_yandex_monthly_stats(months=months)
    except Exception:
        return None
    try:
        from plugins.moysklad.catalog_cache import set_raw_envelope

        set_raw_envelope(
            key, {"payload": stats, "ttl_seconds": _CACHE_TTL_S}, kind="yandex_stats"
        )
    except Exception:
        pass
    return stats


def build_reconciliation(
    month_report: dict[str, Any],
    stats: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Rows «МС vs кабинет Яндекса» for the months both sides know."""
    if not stats:
        return []
    out: list[dict[str, Any]] = []
    for month_id, real in (stats.get("months") or {}).items():
        ms_cell = (month_report.get(month_id) or {}).get("yandex_market") or {}
        ms_turnover = float(ms_cell.get("turnover") or 0)
        buyer = float(real.get("buyer_total") or 0)
        row = {
            "month": month_id,
            "ms_turnover": round(ms_turnover, 2),
            "ms_orders": ms_cell.get("orders"),
            "cabinet_buyer_total": buyer,
            "cabinet_payout_total": real.get("payout_total"),
            "cabinet_orders": real.get("orders"),
        }
        if buyer > 0:
            row["delta"] = round(ms_turnover - buyer, 2)
            row["delta_pct"] = round((ms_turnover - buyer) / buyer, 4)
        out.append(row)
    return out
