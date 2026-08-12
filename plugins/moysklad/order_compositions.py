"""Fetch MoySklad order line items (assortment) → «состав заказа».

Catalog ingest only stores description/name as ``product_snippet``. Real bouquet
composition lives on ``/customerorder/{id}/positions`` — enrich lazily when
building a client card / AI facts so recommendations can name past bouquets.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

# Order names / marketplace codes that are not human bouquet descriptions.
_CODE_LIKE_RE = re.compile(
    r"^[#№]?\s*\d{3,}(?:[-_/]\d+)?$|"
    r"^\d{4}-\d{2}$|"
    r"^(личная\s+продажа|оплата|перевод|доставка)\b",
    re.IGNORECASE,
)


def looks_like_order_code(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return True
    if len(raw) <= 2:
        return True
    if _CODE_LIKE_RE.match(raw):
        return True
    # Pure digits / hash ids
    compact = re.sub(r"[\s#№\-_/]", "", raw)
    return bool(compact) and compact.isdigit() and len(compact) >= 4


def position_lines_from_payload(
    payload: dict[str, Any] | None,
    *,
    max_lines: int = 12,
) -> list[str]:
    """Turn MoySklad positions expand=assortment into «Name ×qty» lines."""
    if not isinstance(payload, dict):
        return []
    lines: list[str] = []
    for pos in list(payload.get("rows") or [])[: max(1, int(max_lines))]:
        if not isinstance(pos, dict):
            continue
        assortment = pos.get("assortment") if isinstance(pos.get("assortment"), dict) else {}
        name = str(
            assortment.get("name") or pos.get("name") or pos.get("assortmentName") or ""
        ).strip()
        if not name:
            continue
        qty = pos.get("quantity")
        if qty not in (None, "", 0, 0.0):
            try:
                qf = float(qty)
                qty_s = str(int(qf)) if qf == int(qf) else str(qf)
            except (TypeError, ValueError):
                qty_s = str(qty).strip()
            lines.append(f"{name} ×{qty_s}")
        else:
            lines.append(name)
    return lines


def composition_text(lines: list[str], *, max_len: int = 240) -> str:
    text = "; ".join(str(x).strip() for x in lines if str(x).strip())
    if len(text) <= max_len:
        return text
    return text[: max(0, max_len - 1)].rstrip() + "…"


def apply_composition_to_order(
    order: dict[str, Any],
    lines: list[str],
) -> dict[str, Any]:
    """Stamp ``line_items`` / ``composition`` / better ``product_snippet``."""
    if not isinstance(order, dict) or not lines:
        return order
    order["line_items"] = list(lines)
    comp = composition_text(lines)
    order["composition"] = comp
    existing = str(order.get("product_snippet") or "").strip()
    desc = str(order.get("description") or "").strip()
    name = str(order.get("name") or "").strip()
    # Prefer real assortment over order codes / empty description.
    if not existing or looks_like_order_code(existing) or existing in {desc, name}:
        order["product_snippet"] = comp
    elif comp and comp not in existing:
        # Keep human description, but surface composition for AI/UI.
        order["product_snippet"] = f"{existing} · {comp}"[:240]
    return order


def enrich_orders_with_compositions(
    orders: list[dict[str, Any]],
    *,
    fetch_positions: Callable[[str], dict[str, Any] | None],
    max_orders: int = 8,
    max_lines: int = 12,
) -> int:
    """Fetch positions for recent orders missing ``line_items``. Returns filled count."""
    if not orders or fetch_positions is None:
        return 0
    # Newest first (moment/date descending) for the seller-facing card.
    indexed = [(i, o) for i, o in enumerate(orders) if isinstance(o, dict)]
    indexed.sort(
        key=lambda pair: str(
            pair[1].get("moment") or pair[1].get("Дата") or pair[1].get("date") or ""
        ),
        reverse=True,
    )
    filled = 0
    for _idx, order in indexed[: max(0, int(max_orders))]:
        existing = order.get("line_items")
        if isinstance(existing, list) and existing:
            # Already enriched (cache / prior card open).
            if not order.get("composition"):
                apply_composition_to_order(order, [str(x) for x in existing if str(x).strip()])
            continue
        oid = str(order.get("id") or "").strip()
        if not oid:
            continue
        try:
            payload = fetch_positions(oid)
        except Exception:
            log.debug("moysklad positions fetch failed for %s", oid, exc_info=True)
            continue
        lines = position_lines_from_payload(payload, max_lines=max_lines)
        if not lines:
            continue
        apply_composition_to_order(order, lines)
        filled += 1
    return filled


def enrich_row_order_compositions(
    row: dict[str, Any],
    *,
    ms_client: Any = None,
    fetch_positions: Optional[Callable[[str], dict[str, Any] | None]] = None,
    max_orders: int = 8,
) -> int:
    """Enrich ``row['_orders_context']`` in place (durable for catalog reuse)."""
    ctx = row.get("_orders_context")
    if not isinstance(ctx, list) or not ctx:
        return 0

    def _fetch(order_id: str) -> dict[str, Any] | None:
        if fetch_positions is not None:
            return fetch_positions(order_id)
        if ms_client is None:
            return None
        try:
            return ms_client.positions(order_id)
        except Exception:
            log.debug("moysklad client.positions failed for %s", order_id, exc_info=True)
            return None

    if fetch_positions is None and ms_client is None:
        return 0
    return enrich_orders_with_compositions(
        ctx, fetch_positions=_fetch, max_orders=max_orders
    )
