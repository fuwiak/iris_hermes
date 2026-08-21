"""Дашборд: CRM-виталс + аналитика Вереск (Excel formulas).

Client-base composition, Telegram reach, send activity, plus day/week/month
P&L from paid MoySklad orders (see dashboard_analytics.py).
"""

from __future__ import annotations

import time
from typing import Any

from plugins.moysklad.audience import (
    row_has_loyalty_points,
    row_has_phone,
    row_is_vip,
    row_matches_entity_type,
)
from plugins.moysklad.sent_history import list_sent_messages
from plugins.moysklad.tg_verify import row_tg_active


def clients_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = {
        "total": 0,
        "individual": 0,
        "legal": 0,
        "entrepreneur": 0,
        "no_type": 0,
        "with_phone": 0,
        "with_loyalty": 0,
        "vip": 0,
        "tg_active": 0,
        "tg_not_found": 0,
        "tg_unchecked": 0,
    }
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        out["total"] += 1
        if row_matches_entity_type(row, "individual"):
            out["individual"] += 1
        elif row_matches_entity_type(row, "legal"):
            out["legal"] += 1
        elif row_matches_entity_type(row, "entrepreneur"):
            out["entrepreneur"] += 1
        else:
            out["no_type"] += 1
        if row_has_phone(row):
            out["with_phone"] += 1
        if row_has_loyalty_points(row):
            out["with_loyalty"] += 1
        if row_is_vip(row):
            out["vip"] += 1
        active = row_tg_active(row)
        if active is True:
            out["tg_active"] += 1
        elif active is False:
            out["tg_not_found"] += 1
        else:
            out["tg_unchecked"] += 1
    return out


def sends_summary(
    messages: list[dict[str, Any]] | None = None,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    from plugins.moysklad.conversations import recency_epoch

    rows = messages if messages is not None else list_sent_messages(limit=1000)
    now_ts = float(now if now is not None else time.time())
    day_ago = now_ts - 24 * 3600
    week_ago = now_ts - 7 * 24 * 3600
    out = {
        "total_logged": len(rows),
        "last_24h": 0,
        "last_7d": 0,
        "delivered_7d": 0,
        "recorded_7d": 0,
        "recent": [],
    }
    for row in rows:
        epoch = recency_epoch(row.get("ts"))
        if epoch >= day_ago:
            out["last_24h"] += 1
        if epoch >= week_ago:
            out["last_7d"] += 1
            if str(row.get("status") or "") == "delivered":
                out["delivered_7d"] += 1
            else:
                out["recorded_7d"] += 1
    out["recent"] = [
        {
            "client_name": r.get("client_name") or r.get("tg_nick") or r.get("client_id"),
            "text": str(r.get("text") or "")[:120],
            "ts": r.get("ts"),
            "status": r.get("status"),
        }
        for r in rows[:8]
    ]
    return out


def build_dashboard_summary(
    rows: list[dict[str, Any]],
    *,
    last_job: dict[str, Any] | None = None,
    messages: list[dict[str, Any]] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    from datetime import date as date_cls
    from plugins.moysklad.dashboard_analytics import build_analytics

    generated = float(now if now is not None else time.time())
    today = date_cls.fromtimestamp(generated)
    return {
        "clients": clients_summary(rows),
        "sends": sends_summary(messages, now=now),
        "last_mass_job": dict(last_job) if isinstance(last_job, dict) else None,
        "analytics": build_analytics(rows, today=today),
        "generated_at": generated,
    }
