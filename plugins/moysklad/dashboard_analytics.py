"""Excel «образец аналитика Вереск» formulas, computed from MoySklad orders.

Sheets ported:
- По дням — daily + month rolls, channel × store (Сокольники/Университет)
- НЕДЕЛЯ — Mon–Sun buckets, WoW %
- МЕСЯЦ — monthly P&L, MoM %, share, margin
- Флау 2024-2025 — FlowWow monthly + year totals + nth-purchase cohorts

Яндекс Маркет (when partner token + yandex_use_cabinet):
  month turnover/orders ← cabinet BUYER totals (not MoySklad list prices)
  deliveries ← analytics_overrides.json, else Yandex delivery commissions
  purchase ← analytics_overrides.json (margin = revenue - purchase * share)

Formulas (do not re-derive):
  growth(new, old)      = (new / old) - 1                 # =(new/old*100-100)/100
  avg_check             = turnover / orders
  revenue               = turnover * (1 - commission_rate)
  commission            = turnover * commission_rate
  share                 = channel_turnover / total_turnover
  margin                = revenue - purchase * share
  platform_commission   = commission / turnover           # =comm*100/оборот/100
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional

from plugins.moysklad.order_status import _parse_moment, classify_order_payment
from plugins.moysklad.sales_channels import is_direct_sales_channel

# Excel «НЕДЕЛЯ» A45 «Расчет фв» and marketplace payouts on МЕСЯЦ.
DEFAULT_COMMISSION: dict[str, float] = {
    "yandex_market": 0.30,  # S10 = S9-(S9*30/100)
    "flavy": 0.30,  # July Флавери: ~138764/196668
    "yandex_eda": 0.25,  # C17 = C16-(C16*25/100)
    "ozon": 0.0,
    "flowwow": 0.346,  # A45 = 0.346
    "floday": 0.346,
    "skyloft": 0.346,
    "direct": 0.0,
    "other": 0.0,
}

CHANNEL_ORDER: tuple[str, ...] = (
    "yandex_market",
    "flavy",
    "yandex_eda",
    "ozon",
    "flowwow",
    "floday",
    "skyloft",
    "direct",
    "other",
)

CHANNEL_LABELS: dict[str, str] = {
    "yandex_market": "Яндекс Маркет",
    "flavy": "Флавери",
    "yandex_eda": "Яндекс Еда",
    "ozon": "OZON",
    "flowwow": "Флау вау",
    "floday": "FloDay",
    "skyloft": "Скайлофт",
    "direct": "Прямые продажи",
    "other": "Прочие",
}

METRIC_ORDER: tuple[str, ...] = (
    "turnover",
    "revenue",
    "margin",
    "orders",
    "avg_check",
    "deliveries",
)

METRIC_LABELS: dict[str, str] = {
    "turnover": "Оборот",
    "revenue": "Выручка",
    "margin": "Маржа",
    "orders": "Заказы",
    "avg_check": "Ср чек",
    "deliveries": "Доставки",
    "commission": "Комиссия",
    "new_clients": "Новые клиенты",
    "second_purchase": "Вторая покупка",
    "third_purchase": "Третья покупка",
    "regular_clients": "Постоянные клиенты",
    "platform_commission": "Комиссия площадки",
}

FLOWWOW_KEYS = frozenset({"flowwow", "floday", "skyloft"})

_MONTHS_RU = (
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
)
_MONTHS_SHORT = (
    "янв",
    "фев",
    "мар",
    "апр",
    "май",
    "июн",
    "июл",
    "авг",
    "сент",
    "окт",
    "ноя",
    "дек",
)


def pct_change(new: float | None, old: float | None) -> float | None:
    """Excel ``=(new/old*100-100)/100``. None when old is 0/missing (DIV/0)."""
    if new is None or old is None:
        return None
    if old == 0:
        return None
    return (float(new) / float(old)) - 1.0


def avg_check(turnover: float, orders: int) -> float | None:
    if orders <= 0:
        return None
    return float(turnover) / float(orders)


def revenue_from_turnover(turnover: float, commission_rate: float) -> float:
    return float(turnover) * (1.0 - float(commission_rate))


def commission_from_turnover(turnover: float, commission_rate: float) -> float:
    return float(turnover) * float(commission_rate)


def share_of_total(part: float, total: float) -> float:
    if total == 0:
        return 0.0
    return float(part) / float(total)


def margin_from_revenue(revenue: float, purchase: float, share: float) -> float:
    """Excel ``=revenue - purchase * share`` (C11 = C10-C48*D9/100)."""
    return float(revenue) - float(purchase) * float(share)


def _norm(text: str) -> str:
    return " ".join(str(text or "").strip().lower().replace("ё", "е").split())


def classify_analytics_channel(channel: str | None) -> str:
    """Map a MoySklad sales-channel name onto an Excel bucket."""
    raw = str(channel or "").strip()
    if not raw:
        return "other"
    t = _norm(raw)
    compact = t.replace(" ", "").replace(".", "").replace("-", "")
    if "skyloft" in compact or "скайлофт" in compact:
        return "skyloft"
    if "floday" in compact or "флодей" in compact or "флодай" in compact:
        return "floday"
    if (
        "flowwow" in compact
        or "флау" in compact
        or compact in {"fw", "фв"}
    ):
        return "flowwow"
    if "ozon" in compact or "озон" in compact:
        return "ozon"
    if "яндекс" in t or "yandex" in t:
        if any(tok in compact for tok in ("еда", "eda", "food", "еды")):
            return "yandex_eda"
        return "yandex_market"
    if "флавери" in compact or "flavy" in compact or "flaveri" in compact:
        return "flavy"
    if is_direct_sales_channel(raw):
        return "direct"
    return "other"


def classify_store(channel: str | None) -> str:
    t = _norm(channel or "")
    if "сокольники" in t:
        return "sokolniki"
    if "университет" in t:
        return "universitet"
    return "unknown"


def parse_order_date(raw: Any) -> Optional[date]:
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    return _parse_moment(raw)


def week_start(d: date) -> date:
    """Monday of the ISO week — Excel НЕДЕЛЯ «1-7 сент»."""
    return d - timedelta(days=d.weekday())


def week_label(start: date) -> str:
    end = start + timedelta(days=6)
    if start.month == end.month:
        return f"{start.day}–{end.day} {_MONTHS_SHORT[start.month - 1]}"
    return (
        f"{start.day} {_MONTHS_SHORT[start.month - 1]} – "
        f"{end.day} {_MONTHS_SHORT[end.month - 1]}"
    )


def month_label(year: int, month: int, *, with_year: bool = False) -> str:
    name = _MONTHS_RU[month - 1]
    if with_year:
        return f"{name} {year}"
    return name


def _order_amount(order: dict[str, Any]) -> float:
    raw = order.get("sum")
    if raw is None:
        raw = order.get("Сумма")
    try:
        return float(raw or 0)
    except (TypeError, ValueError):
        return 0.0


def _order_channel(order: dict[str, Any]) -> str:
    return str(order.get("channel") or order.get("Канал продаж") or "").strip()


def analytics_paid_only() -> bool:
    """Count only explicitly paid orders instead of all non-cancelled ones.

    Off by default: the client's Excel counts every completed order, while
    marketplace orders in MoySklad often carry no per-order payment stamp
    (payout arrives as one transfer) — filtering on «paid» silently dropped
    them and every dashboard figure came out short of the reference report.
    """
    raw = (os.getenv("MOYSKLAD_ANALYTICS_PAID_ONLY") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def iter_paid_orders(
    rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Flatten catalog ``_orders_context`` into analytic facts (deduped).

    Cancelled orders are always excluded; unpaid/unknown are counted unless
    ``MOYSKLAD_ANALYTICS_PAID_ONLY`` restores the strict old behavior.
    """
    paid_only = analytics_paid_only()
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        client_id = str(row.get("id") or "").strip()
        ctx = row.get("_orders_context")
        if not isinstance(ctx, list):
            continue
        for order in ctx:
            if not isinstance(order, dict):
                continue
            payment = classify_order_payment(order)
            if payment == "cancelled":
                continue
            if paid_only and payment != "paid":
                continue
            amount = _order_amount(order)
            if amount <= 0:
                continue
            day = parse_order_date(
                order.get("moment") or order.get("Дата") or order.get("date")
            )
            if day is None:
                continue
            oid = str(order.get("id") or "").strip()
            key = oid or f"{client_id}:{day.isoformat()}:{amount}:{_order_channel(order)}"
            if key in seen:
                continue
            seen.add(key)
            channel_name = _order_channel(order)
            out.append(
                {
                    "id": oid or key,
                    "client_id": client_id,
                    "date": day,
                    "amount": amount,
                    "channel_name": channel_name,
                    "channel": classify_analytics_channel(channel_name),
                    "store": classify_store(channel_name),
                }
            )
    return out


def _empty_cell() -> dict[str, Any]:
    return {
        "orders": 0,
        "turnover": 0.0,
        "deliveries": 0.0,
        "sokolniki_orders": 0,
        "sokolniki_turnover": 0.0,
        "universitet_orders": 0,
        "universitet_turnover": 0.0,
    }


def _add_order(cell: dict[str, Any], fact: dict[str, Any]) -> None:
    cell["orders"] += 1
    cell["turnover"] += fact["amount"]
    store = fact["store"]
    if store == "sokolniki":
        cell["sokolniki_orders"] += 1
        cell["sokolniki_turnover"] += fact["amount"]
    elif store == "universitet":
        cell["universitet_orders"] += 1
        cell["universitet_turnover"] += fact["amount"]


def _finish_cell(
    cell: dict[str, Any],
    *,
    commission_rate: float,
    purchase: float,
    total_turnover: float,
) -> dict[str, Any]:
    turnover = float(cell["turnover"])
    orders = int(cell["orders"])
    rev = revenue_from_turnover(turnover, commission_rate)
    comm = commission_from_turnover(turnover, commission_rate)
    sh = share_of_total(turnover, total_turnover)
    deliveries = float(cell.get("deliveries") or 0)
    return {
        "orders": orders,
        "turnover": round(turnover, 2),
        "revenue": round(rev, 2),
        "commission": round(comm, 2),
        "avg_check": None if orders <= 0 else round(turnover / orders, 2),
        "deliveries": round(deliveries, 2),
        "share": round(sh, 6),
        "margin": round(margin_from_revenue(rev, purchase, sh), 2),
        "sokolniki_orders": int(cell["sokolniki_orders"]),
        "sokolniki_turnover": round(float(cell["sokolniki_turnover"]), 2),
        "universitet_orders": int(cell["universitet_orders"]),
        "universitet_turnover": round(float(cell["universitet_turnover"]), 2),
    }


def _growth_series(values: list[float | None]) -> list[float | None]:
    out: list[float | None] = []
    for i, val in enumerate(values):
        if i == 0:
            out.append(None)
        else:
            out.append(pct_change(val, values[i - 1]))
    return out


def _channel_metric_series(
    periods: list[str],
    cells: dict[tuple[str, str], dict[str, Any]],
    channel: str,
    metric: str,
) -> list[float | None]:
    series: list[float | None] = []
    for pid in periods:
        cell = cells.get((pid, channel))
        if not cell:
            series.append(
                0.0
                if metric
                in ("orders", "turnover", "revenue", "margin", "commission", "deliveries")
                else None
            )
            continue
        series.append(cell.get(metric))
    return series


def _used_channels(facts: list[dict[str, Any]]) -> list[str]:
    present = {f["channel"] for f in facts}
    ordered = [k for k in CHANNEL_ORDER if k in present]
    if "other" in present and "other" not in ordered:
        ordered.append("other")
    return ordered or list(CHANNEL_ORDER[:-1])


def _period_totals(
    periods: list[str],
    channels: list[str],
    finished: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, list[float | None]]:
    turnover: list[float | None] = []
    revenue: list[float | None] = []
    margin: list[float | None] = []
    orders: list[float | None] = []
    for pid in periods:
        t = r = m = 0.0
        o = 0
        for ch in channels:
            cell = finished.get((pid, ch))
            if not cell:
                continue
            t += float(cell["turnover"])
            r += float(cell["revenue"])
            m += float(cell["margin"])
            o += int(cell["orders"])
        turnover.append(round(t, 2))
        revenue.append(round(r, 2))
        margin.append(round(m, 2))
        orders.append(float(o))
    avg_check = [
        None if int(o or 0) <= 0 else round(float(t) / float(o), 2)
        for t, o in zip(turnover, orders)
    ]
    return {
        "turnover": turnover,
        "revenue": revenue,
        "margin": margin,
        "orders": orders,
        "avg_check": avg_check,
        "growth": {
            "turnover": _growth_series(turnover),
            "revenue": _growth_series(revenue),
            "margin": _growth_series(margin),
            "orders": _growth_series(orders),
            "avg_check": _growth_series(avg_check),
        },
    }


def _pack_channel_block(
    periods: list[str],
    channel: str,
    finished: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    metrics: dict[str, list[float | None]] = {}
    for metric in METRIC_ORDER:
        metrics[metric] = _channel_metric_series(periods, finished, channel, metric)
    metrics["growth"] = {m: _growth_series(metrics[m]) for m in METRIC_ORDER}
    return {
        "key": channel,
        "label": CHANNEL_LABELS.get(channel, channel),
        "commission_rate": DEFAULT_COMMISSION.get(channel, 0.0),
        **metrics,
    }


def _finish_grid(
    raw: dict[tuple[str, str], dict[str, Any]],
    periods: list[str],
    channels: list[str],
    *,
    purchase_by_period: dict[str, float] | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    purchase_by_period = purchase_by_period or {}
    totals: dict[str, float] = {}
    for pid in periods:
        totals[pid] = sum(
            float(raw.get((pid, ch), {}).get("turnover") or 0) for ch in channels
        )
    finished: dict[tuple[str, str], dict[str, Any]] = {}
    for pid in periods:
        purchase = float(purchase_by_period.get(pid) or 0)
        total = totals.get(pid) or 0.0
        for ch in channels:
            cell = raw.get((pid, ch)) or _empty_cell()
            finished[(pid, ch)] = _finish_cell(
                cell,
                commission_rate=DEFAULT_COMMISSION.get(ch, 0.0),
                purchase=purchase,
                total_turnover=total,
            )
    return finished


def _build_matrix(
    periods: list[dict[str, Any]],
    channels: list[str],
    finished: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    pids = [p["id"] for p in periods]
    return {
        "periods": periods,
        "channels": [_pack_channel_block(pids, ch, finished) for ch in channels],
        "totals": _period_totals(pids, channels, finished),
    }


def _nth_purchase_counts(
    facts: list[dict[str, Any]],
    period_of,
) -> dict[str, dict[str, int]]:
    """Per-period FlowWow cohort counts (Excel Флау rows 8–11)."""
    by_client: dict[str, list[date]] = defaultdict(list)
    for fact in facts:
        if fact["channel"] not in FLOWWOW_KEYS:
            continue
        cid = fact["client_id"] or fact["id"]
        by_client[cid].append(fact["date"])
    buckets: dict[str, dict[str, int]] = defaultdict(
        lambda: {"new_clients": 0, "second_purchase": 0, "third_purchase": 0, "regular_clients": 0}
    )
    for dates in by_client.values():
        dates.sort()
        for idx, day in enumerate(dates, start=1):
            pid = period_of(day)
            if not pid:
                continue
            if idx == 1:
                buckets[pid]["new_clients"] += 1
            elif idx == 2:
                buckets[pid]["second_purchase"] += 1
            elif idx == 3:
                buckets[pid]["third_purchase"] += 1
            else:
                buckets[pid]["regular_clients"] += 1
    return buckets


def _rub(n: float) -> str:
    return f"{int(round(float(n))):,}".replace(",", " ")


def _pct_ru(n: float) -> str:
    pct = n * 100
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.0f}%"


def _series_at(seq: list[Any] | None, idx: int) -> float | None:
    if not seq or idx >= len(seq) or idx < -len(seq):
        return None
    val = seq[idx]
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def build_insights(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Hot takes from the already-computed Excel matrices. No extra I/O."""
    by_month = payload.get("by_month") or {}
    channels = list(by_month.get("channels") or [])
    periods = list(by_month.get("periods") or [])
    totals = by_month.get("totals") or {}
    if not periods or not channels:
        return []
    last = -1
    label = str((periods[last] or {}).get("label") or "месяц")
    total_t = _series_at(totals.get("turnover"), last) or 0.0
    total_r = _series_at(totals.get("revenue"), last) or 0.0
    out: list[dict[str, Any]] = []

    shares: list[tuple[dict[str, Any], float, float]] = []
    for ch in channels:
        t = _series_at(ch.get("turnover"), last) or 0.0
        if t > 0 and total_t > 0:
            shares.append((ch, t, t / total_t))
    shares.sort(key=lambda row: -row[1])
    if shares and shares[0][2] >= 0.45:
        ch, t, sh = shares[0]
        out.append(
            {
                "id": "concentration",
                "tone": "warn",
                "title": f"{ch.get('label')} — {sh:.0%} оборота",
                "body": f"{label}: {_rub(t)} ₽. Один канал тянет кассу — риск площадки.",
                "channel": ch.get("key"),
                "metric": "turnover",
                "scope": "month",
            }
        )

    movers: list[tuple[dict[str, Any], float, float]] = []
    for ch in channels:
        g = _series_at((ch.get("growth") or {}).get("turnover"), last)
        cur = _series_at(ch.get("turnover"), last) or 0.0
        if g is None:
            continue
        movers.append((ch, g, cur))
    if movers:
        up = max(movers, key=lambda row: row[1])
        down = min(movers, key=lambda row: row[1])
        if up[1] > 0.05:
            out.append(
                {
                    "id": "mom-up",
                    "tone": "up",
                    "title": f"{up[0].get('label')} {_pct_ru(up[1])}",
                    "body": f"Самый резкий рост к прошлому месяцу. Оборот {_rub(up[2])} ₽.",
                    "channel": up[0].get("key"),
                    "metric": "turnover",
                    "scope": "month",
                }
            )
        if down[1] < -0.05 and (up[1] <= 0.05 or down[0].get("key") != up[0].get("key")):
            out.append(
                {
                    "id": "mom-down",
                    "tone": "down",
                    "title": f"{down[0].get('label')} {_pct_ru(down[1])}",
                    "body": f"Главная просадка месяца. Оборот {_rub(down[2])} ₽.",
                    "channel": down[0].get("key"),
                    "metric": "turnover",
                    "scope": "month",
                }
            )

    g_orders = _series_at((totals.get("growth") or {}).get("orders"), last)
    g_check = _series_at((totals.get("growth") or {}).get("avg_check"), last)
    if g_orders is not None and g_check is not None:
        if g_orders > 0.05 and g_check < -0.05:
            out.append(
                {
                    "id": "cheap-mix",
                    "tone": "warn",
                    "title": "Заказов больше, чек ниже",
                    "body": (
                        f"Заказы {_pct_ru(g_orders)}, ср. чек {_pct_ru(g_check)}. "
                        "Микс дешевеет — смотреть средний букет."
                    ),
                    "channel": None,
                    "metric": "avg_check",
                    "scope": "month",
                }
            )
        elif g_orders < -0.05 and g_check > 0.05:
            out.append(
                {
                    "id": "premium-or-traffic",
                    "tone": "info",
                    "title": "Меньше заказов, чек выше",
                    "body": (
                        f"Заказы {_pct_ru(g_orders)}, ср. чек {_pct_ru(g_check)}. "
                        "Либо премиум, либо просел трафик."
                    ),
                    "channel": None,
                    "metric": "avg_check",
                    "scope": "month",
                }
            )

    if total_t > 0 and total_r >= 0:
        bite = 1.0 - (total_r / total_t)
        if bite >= 0.22:
            out.append(
                {
                    "id": "commission-bite",
                    "tone": "warn",
                    "title": f"Комиссия съела {bite:.0%} оборота",
                    "body": (
                        f"Выручка {_rub(total_r)} ₽ из {_rub(total_t)} ₽. "
                        "Маркетплейсы дорожают вход."
                    ),
                    "channel": None,
                    "metric": "revenue",
                    "scope": "month",
                }
            )

    week_tot = (payload.get("by_week") or {}).get("totals") or {}
    wow = _series_at((week_tot.get("growth") or {}).get("turnover"), -1)
    week_periods = list((payload.get("by_week") or {}).get("periods") or [])
    if wow is not None and abs(wow) >= 0.12 and week_periods:
        wlabel = week_periods[-1].get("label") or "неделя"
        out.append(
            {
                "id": "wow",
                "tone": "up" if wow > 0 else "down",
                "title": f"Неделя {wlabel}: {_pct_ru(wow)}",
                "body": "Скачок WoW по общему обороту. Откройте график по неделям.",
                "channel": None,
                "metric": "turnover",
                "scope": "week",
            }
        )

    fw = payload.get("flowwow") or {}
    metrics = fw.get("metrics") or {}
    fw_periods = list(fw.get("periods") or [])
    if fw_periods:
        new_c = _series_at(metrics.get("new_clients"), -1) or 0.0
        second = _series_at(metrics.get("second_purchase"), -1) or 0.0
        if new_c >= 8 and second / new_c < 0.08:
            out.append(
                {
                    "id": "fw-repeat",
                    "tone": "warn",
                    "title": "Флау почти без второй покупки",
                    "body": (
                        f"{int(new_c)} новых vs {int(second)} вторых. "
                        "Деньги в первом касании — повтор не цепляется."
                    ),
                    "channel": "flowwow",
                    "metric": "turnover",
                    "scope": "month",
                }
            )

    # Dedup by id, cap so the board stays scannable.
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for row in out:
        rid = str(row.get("id") or "")
        if rid in seen:
            continue
        seen.add(rid)
        unique.append(row)
    return unique[:6]


def apply_yandex_cabinet_to_raw(
    raw: dict[tuple[str, str], dict[str, Any]],
    *,
    yandex_cabinet: dict[str, Any] | None,
    deliveries_by_month: dict[str, float] | None = None,
    use_cabinet: bool = True,
) -> list[str]:
    """Reprice Яндекс Маркет month cells from partner API + manual fills.

    - ``turnover`` ← cabinet BUYER total (what buyers paid — Excel-aligned base)
    - ``orders`` ← cabinet order count when present
    - ``deliveries`` ← override file, else Yandex delivery commissions
    """
    notes: list[str] = []
    deliveries_by_month = {
        str(k): float(v) for k, v in (deliveries_by_month or {}).items()
    }
    months = (yandex_cabinet or {}).get("months") or {}
    touched_cabinet = 0
    month_ids = set(months) | set(deliveries_by_month)
    for month_id in month_ids:
        key = (month_id, "yandex_market")
        real = months.get(month_id) or {}
        cell = dict(raw.get(key) or _empty_cell())
        if use_cabinet:
            buyer = float(real.get("buyer_total") or 0)
            if buyer > 0:
                cell["turnover"] = buyer
                if real.get("orders"):
                    cell["orders"] = int(real["orders"])
                # Cabinet has no store split — zero the MS-derived split.
                cell["sokolniki_orders"] = 0
                cell["sokolniki_turnover"] = 0.0
                cell["universitet_orders"] = 0
                cell["universitet_turnover"] = 0.0
                touched_cabinet += 1
        if month_id in deliveries_by_month:
            cell["deliveries"] = float(deliveries_by_month[month_id])
        elif real.get("deliveries") is not None:
            cell["deliveries"] = float(real.get("deliveries") or 0)
        raw[key] = cell
    if touched_cabinet:
        notes.append(
            "Яндекс Маркет: оборот и заказы из кабинета (BUYER), не цены МС до скидки."
        )
    if deliveries_by_month:
        notes.append(
            "Доставки: ручные значения из analytics_overrides.json (как в Excel)."
        )
    elif any(float((m or {}).get("deliveries") or 0) > 0 for m in months.values()):
        notes.append(
            "Доставки: комиссии доставки Яндекса (EXPRESS_DELIVERY…); "
            "для Excel-цифр задайте deliveries_by_month в overrides."
        )
    return notes


def build_analytics(
    rows: list[dict[str, Any]],
    *,
    today: date | None = None,
    day_limit: int = 62,
    week_limit: int = 16,
    month_limit: int = 14,
    purchase_by_month: dict[str, float] | None = None,
    deliveries_by_month: dict[str, float] | None = None,
    yandex_cabinet: dict[str, Any] | None = None,
    yandex_use_cabinet: bool = True,
) -> dict[str, Any]:
    today = today or date.today()
    facts = iter_paid_orders(rows)
    channels = _used_channels(facts)

    # --- days ---
    day_from = today - timedelta(days=max(1, int(day_limit)) - 1)
    day_ids: list[str] = []
    d = day_from
    while d <= today:
        day_ids.append(d.isoformat())
        d += timedelta(days=1)
    raw_days: dict[tuple[str, str], dict[str, Any]] = defaultdict(_empty_cell)
    raw_months_from_days: dict[tuple[str, str], dict[str, Any]] = defaultdict(_empty_cell)
    for fact in facts:
        month_id = f"{fact['date'].year:04d}-{fact['date'].month:02d}"
        _add_order(raw_months_from_days[(month_id, fact["channel"])], fact)
        if day_from <= fact["date"] <= today:
            _add_order(raw_days[(fact["date"].isoformat(), fact["channel"])], fact)

    # Month summary rows that sit above daily rows in «По дням».
    month_ids_for_days: list[str] = []
    seen_m: set[str] = set()
    for did in day_ids:
        mid = did[:7]
        if mid not in seen_m:
            seen_m.add(mid)
            month_ids_for_days.append(mid)
    cabinet_notes = apply_yandex_cabinet_to_raw(
        raw_months_from_days,
        yandex_cabinet=yandex_cabinet,
        deliveries_by_month=deliveries_by_month,
        use_cabinet=yandex_use_cabinet,
    )
    finished_day_months = _finish_grid(
        raw_months_from_days, month_ids_for_days, channels, purchase_by_period=purchase_by_month
    )
    finished_days = _finish_grid(raw_days, day_ids, channels)

    day_rows: list[dict[str, Any]] = []
    emitted_months: set[str] = set()
    for did in day_ids:
        mid = did[:7]
        if mid not in emitted_months:
            emitted_months.add(mid)
            y, mo = int(mid[:4]), int(mid[5:7])
            day_rows.append(
                {
                    "id": mid,
                    "kind": "month",
                    "label": month_label(y, mo, with_year=True),
                    "channels": {
                        ch: finished_day_months.get((mid, ch)) or _finish_cell(
                            _empty_cell(),
                            commission_rate=DEFAULT_COMMISSION.get(ch, 0.0),
                            purchase=float((purchase_by_month or {}).get(mid) or 0),
                            total_turnover=0.0,
                        )
                        for ch in channels
                    },
                }
            )
        day = date.fromisoformat(did)
        day_rows.append(
            {
                "id": did,
                "kind": "day",
                "label": f"{day.day:02d}.{day.month:02d}",
                "channels": {
                    ch: finished_days.get((did, ch)) or _finish_cell(
                        _empty_cell(),
                        commission_rate=DEFAULT_COMMISSION.get(ch, 0.0),
                        purchase=0.0,
                        total_turnover=0.0,
                    )
                    for ch in channels
                },
            }
        )

    # --- weeks ---
    this_week = week_start(today)
    week_starts = [
        this_week - timedelta(weeks=i)
        for i in range(max(1, int(week_limit)) - 1, -1, -1)
    ]
    week_periods = [
        {
            "id": ws.isoformat(),
            "label": week_label(ws),
            "start": ws.isoformat(),
            "end": (ws + timedelta(days=6)).isoformat(),
        }
        for ws in week_starts
    ]
    week_ids = [p["id"] for p in week_periods]
    week_set = set(week_ids)
    raw_weeks: dict[tuple[str, str], dict[str, Any]] = defaultdict(_empty_cell)
    for fact in facts:
        pid = week_start(fact["date"]).isoformat()
        if pid in week_set:
            _add_order(raw_weeks[(pid, fact["channel"])], fact)
    finished_weeks = _finish_grid(raw_weeks, week_ids, channels)
    by_week = _build_matrix(week_periods, channels, finished_weeks)

    # --- months ---
    month_cursor = date(today.year, today.month, 1)
    month_periods: list[dict[str, Any]] = []
    for i in range(max(1, int(month_limit)) - 1, -1, -1):
        y = month_cursor.year
        m = month_cursor.month - i
        while m <= 0:
            m += 12
            y -= 1
        while m > 12:
            m -= 12
            y += 1
        pid = f"{y:04d}-{m:02d}"
        month_periods.append(
            {
                "id": pid,
                "label": month_label(y, m, with_year=True),
                "year": y,
                "month": m,
            }
        )
    month_ids = [p["id"] for p in month_periods]
    month_set = set(month_ids)
    raw_months: dict[tuple[str, str], dict[str, Any]] = defaultdict(_empty_cell)
    for fact in facts:
        pid = f"{fact['date'].year:04d}-{fact['date'].month:02d}"
        if pid in month_set:
            _add_order(raw_months[(pid, fact["channel"])], fact)
    cabinet_notes = apply_yandex_cabinet_to_raw(
        raw_months,
        yandex_cabinet=yandex_cabinet,
        deliveries_by_month=deliveries_by_month,
        use_cabinet=yandex_use_cabinet,
    )
    finished_months = _finish_grid(
        raw_months, month_ids, channels, purchase_by_period=purchase_by_month
    )
    by_month = _build_matrix(month_periods, channels, finished_months)

    # --- FlowWow sheet ---
    fw_facts = [f for f in facts if f["channel"] in FLOWWOW_KEYS]
    fw_month_ids: list[str] = []
    if fw_facts:
        first = min(f["date"] for f in fw_facts)
        cur = date(first.year, first.month, 1)
        last = date(today.year, today.month, 1)
        while cur <= last:
            fw_month_ids.append(f"{cur.year:04d}-{cur.month:02d}")
            if cur.month == 12:
                cur = date(cur.year + 1, 1, 1)
            else:
                cur = date(cur.year, cur.month + 1, 1)
    else:
        fw_month_ids = month_ids
    fw_set = set(fw_month_ids)
    raw_fw: dict[tuple[str, str], dict[str, Any]] = defaultdict(_empty_cell)
    for fact in fw_facts:
        pid = f"{fact['date'].year:04d}-{fact['date'].month:02d}"
        if pid in fw_set:
            # One FlowWow row = all FW-family channels combined, matching «Флау вау».
            _add_order(raw_fw[(pid, "flowwow")], fact)
    finished_fw = _finish_grid(raw_fw, fw_month_ids, ["flowwow"])
    nth = _nth_purchase_counts(
        fw_facts, lambda day: f"{day.year:04d}-{day.month:02d}" if f"{day.year:04d}-{day.month:02d}" in fw_set else ""
    )

    fw_periods = []
    turnover: list[float | None] = []
    orders_s: list[float | None] = []
    revenue: list[float | None] = []
    commission: list[float | None] = []
    new_c: list[float | None] = []
    second: list[float | None] = []
    third: list[float | None] = []
    regular: list[float | None] = []
    for pid in fw_month_ids:
        y, m = int(pid[:4]), int(pid[5:7])
        fw_periods.append({"id": pid, "label": month_label(y, m, with_year=True), "year": y})
        cell = finished_fw.get((pid, "flowwow")) or _finish_cell(
            _empty_cell(), commission_rate=DEFAULT_COMMISSION["flowwow"], purchase=0.0, total_turnover=0.0
        )
        turnover.append(cell["turnover"])
        orders_s.append(float(cell["orders"]))
        revenue.append(cell["revenue"])
        commission.append(cell["commission"])
        bucket = nth.get(pid) or {}
        new_c.append(float(bucket.get("new_clients") or 0))
        second.append(float(bucket.get("second_purchase") or 0))
        third.append(float(bucket.get("third_purchase") or 0))
        regular.append(float(bucket.get("regular_clients") or 0))

    avg = [
        None if not o else round(float(t) / float(o), 2)
        for t, o in zip(turnover, orders_s)
    ]
    platform = [
        None if not t else round(float(c) / float(t), 6)
        for t, c in zip(turnover, commission)
    ]

    def _year_sum(year: int, series: list[float | None], pids: list[str]) -> float:
        total = 0.0
        for pid, val in zip(pids, series):
            if pid.startswith(f"{year:04d}-") and val:
                total += float(val)
        return round(total, 2)

    years = sorted({int(p["year"]) for p in fw_periods})
    year_totals = {
        str(y): {
            "turnover": _year_sum(y, turnover, fw_month_ids),
            "orders": _year_sum(y, orders_s, fw_month_ids),
            "revenue": _year_sum(y, revenue, fw_month_ids),
            "commission": _year_sum(y, commission, fw_month_ids),
        }
        for y in years
    }
    for y, block in year_totals.items():
        block["avg_check"] = (
            None
            if not block["orders"]
            else round(block["turnover"] / block["orders"], 2)
        )
        block["platform_commission"] = (
            None
            if not block["turnover"]
            else round(block["commission"] / block["turnover"], 6)
        )

    flowwow = {
        "periods": fw_periods,
        "metrics": {
            "turnover": turnover,
            "orders": orders_s,
            "avg_check": avg,
            "commission": commission,
            "revenue": revenue,
            "new_clients": new_c,
            "second_purchase": second,
            "third_purchase": third,
            "regular_clients": regular,
            "platform_commission": platform,
            "growth": {
                "turnover": _growth_series(turnover),
                "orders": _growth_series(orders_s),
                "avg_check": _growth_series(avg),
                "commission": _growth_series(commission),
                "revenue": _growth_series(revenue),
                "new_clients": _growth_series(new_c),
                "second_purchase": _growth_series(second),
                "third_purchase": _growth_series(third),
                "regular_clients": _growth_series(regular),
            },
        },
        "year_totals": year_totals,
        "unavailable": [
            "Конверсия FW",
            "Магазин в избранное",
            "Товары в подборки",
            "Конверсия город",
        ],
    }

    latest = by_month["totals"]
    kpi = {
        "turnover": latest["turnover"][-1] if latest["turnover"] else 0,
        "revenue": latest["revenue"][-1] if latest["revenue"] else 0,
        "orders": latest["orders"][-1] if latest["orders"] else 0,
        "avg_check": latest["avg_check"][-1] if latest["avg_check"] else None,
        "margin": latest["margin"][-1] if latest["margin"] else 0,
        "mom_turnover": (latest.get("growth") or {}).get("turnover", [None])[-1]
        if latest["turnover"]
        else None,
        "period": month_periods[-1]["label"] if month_periods else "",
    }
    payload = {
        "formulas": {
            "growth": "(new/old)-1",
            "avg_check": "turnover/orders",
            "revenue": "turnover*(1-commission_rate)",
            "commission": "turnover*commission_rate",
            "share": "channel_turnover/total_turnover",
            "margin": "revenue-purchase*share",
            "platform_commission": "commission/turnover",
        },
        "commission_rates": dict(DEFAULT_COMMISSION),
        "channel_labels": {k: CHANNEL_LABELS[k] for k in channels},
        "metric_labels": dict(METRIC_LABELS),
        "kpi": kpi,
        "by_day": {"rows": day_rows, "channels": channels},
        "by_week": by_week,
        "by_month": by_month,
        "flowwow": flowwow,
        "order_count": len(facts),
        "notes": [
            "Оплаченные заказы из кэша МойСклад (день/неделя). Месяц Яндекс — кабинет BUYER при наличии токена.",
            "Маржа = выручка − закупка×доля. Закупка и Excel-доставки — в analytics_overrides.json.",
            *cabinet_notes,
        ],
        "yandex_source": (
            "cabinet"
            if yandex_use_cabinet and (yandex_cabinet or {}).get("months")
            else "moysklad"
        ),
    }
    payload["insights"] = build_insights(payload)
    return payload
