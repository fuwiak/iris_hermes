"""Order payment / outcome helpers for MoySklad CRM.

Distinguishes paid vs unpaid vs cancelled so AI does not treat failed
checkouts as real customers or chase stale «где оплата?» years later.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

_CANCEL_RE = re.compile(
    r"отмен|cancel|reject|аннул|удал|не\s*состоя|отказ",
    re.IGNORECASE,
)
# Recent unpaid → soft payment chase; older unpaid → failed / abandoned.
_RECENT_UNPAID_DAYS = 90


def _parse_moment(raw: Any) -> Optional[date]:
    text = str(raw or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19], fmt).date()
        except ValueError:
            continue
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            return date(int(text[0:4]), int(text[5:7]), int(text[8:10]))
        except ValueError:
            return None
    return None


def classify_order_payment(order: dict[str, Any]) -> str:
    """Return ``paid`` | ``unpaid`` | ``cancelled`` | ``unknown``."""
    if not isinstance(order, dict):
        return "unknown"
    if order.get("applicable") is False:
        return "cancelled"
    state = str(order.get("state") or order.get("state_name") or "").strip()
    if state and _CANCEL_RE.search(state):
        return "cancelled"
    # Explicit stamp from ingest
    stamped = str(order.get("payment_status") or "").strip().lower()
    if stamped in ("paid", "unpaid", "cancelled", "unknown"):
        if stamped != "unknown":
            return stamped
    unpaid = order.get("unpaid")
    if unpaid is not None and unpaid != "":
        try:
            unpaid_f = float(unpaid)
        except (TypeError, ValueError):
            unpaid_f = None
        if unpaid_f is not None:
            if unpaid_f > 0.009:
                return "unpaid"
            return "paid"
    payed = order.get("payed_sum")
    if payed is not None and payed != "":
        try:
            if float(payed) > 0.009:
                return "paid"
        except (TypeError, ValueError):
            pass
    amount = order.get("sum")
    if amount is None:
        amount = order.get("Сумма")
    try:
        amount_f = float(amount or 0)
    except (TypeError, ValueError):
        amount_f = 0.0
    if amount_f <= 0:
        return "unknown"
    return "unknown"


def order_is_recent(
    order: dict[str, Any],
    *,
    within_days: int = _RECENT_UNPAID_DAYS,
    today: Optional[date] = None,
) -> bool:
    today = today or date.today()
    moment = _parse_moment(order.get("moment") or order.get("date") or order.get("Дата"))
    if moment is None:
        return False
    return moment >= (today - timedelta(days=max(1, int(within_days))))


def summarize_order_context(
    orders: list[dict[str, Any]] | None,
    *,
    today: Optional[date] = None,
) -> dict[str, Any]:
    """Aggregate payment outcomes for a client's order list."""
    today = today or date.today()
    items = [o for o in (orders or []) if isinstance(o, dict)]
    paid = 0
    unpaid = 0
    cancelled = 0
    unknown = 0
    recent_unpaid = 0
    stale_unpaid = 0
    paid_amounts: list[float] = []
    last_paid = ""
    last_any = ""
    for o in items:
        status = classify_order_payment(o)
        o["payment_status"] = status
        moment = str(o.get("moment") or o.get("date") or o.get("Дата") or "").strip()
        if moment and moment > last_any:
            last_any = moment
        if status == "paid":
            paid += 1
            if moment and moment > last_paid:
                last_paid = moment
            try:
                amt = float(o.get("sum") or o.get("Сумма") or 0)
            except (TypeError, ValueError):
                amt = 0.0
            if amt > 0:
                paid_amounts.append(amt)
        elif status == "unpaid":
            unpaid += 1
            if order_is_recent(o, today=today):
                recent_unpaid += 1
            else:
                stale_unpaid += 1
        elif status == "cancelled":
            cancelled += 1
        else:
            unknown += 1

    total = len(items)
    fulfilled = paid  # only paid count as real customers
    # Failed / abandoned: unpaid+cancelled only, and nothing recent to chase.
    failed_only = (
        total > 0
        and fulfilled == 0
        and (unpaid + cancelled) > 0
        and recent_unpaid == 0
    )
    # unknown-only (no payedSum) → not failed; treat cautiously as thin history
    outcome = "none"
    if total <= 0:
        outcome = "none"
    elif failed_only:
        outcome = "failed"
    elif recent_unpaid > 0 and fulfilled == 0:
        outcome = "pending_payment"
    elif fulfilled >= 1:
        outcome = "customer"
    else:
        outcome = "unknown"

    avg_paid = round(sum(paid_amounts) / len(paid_amounts), 2) if paid_amounts else None
    return {
        "order_count": total,
        "paid_order_count": paid,
        "unpaid_order_count": unpaid,
        "cancelled_order_count": cancelled,
        "unknown_order_count": unknown,
        "fulfilled_order_count": fulfilled,
        "recent_unpaid_count": recent_unpaid,
        "stale_unpaid_count": stale_unpaid,
        "failed_only": failed_only,
        "customer_outcome": outcome,
        "avg_check_paid": avg_paid,
        "last_paid_order_at": last_paid or None,
        "last_order_at": last_any or None,
    }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
