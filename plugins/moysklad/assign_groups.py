"""Heuristic MoySklad group assignment (Iris CRM _heuristic_group + keywords)."""

from __future__ import annotations

import re
from typing import Any

from plugins.moysklad.client import MoySkladClient
from plugins.moysklad.sales_channels import (
    SALES_CHANNEL_TYPE_DIRECT,
    SALES_CHANNEL_TYPE_MARKETPLACE,
    moysklad_group_tokens,
    sales_channel_type_from_channels,
)

_MONTH_INDEX_TO_EVENT = {
    1: "событие января",
    2: "событие февраля",
    3: "событие марта",
    4: "событие апреля",
    5: "событие мая",
    6: "событие июня",
    7: "событие июля",
    8: "событие августа",
    9: "событие сентября",
    10: "событие октября",
    11: "событие ноября",
    12: "событие декабря",
}

_KEYWORD_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("8 марта", ("8 марта", "8марта", "8-го марта")),
    (
        "день мам",
        (
            "день мам",
            "день мамы",
            "день матери",
            "для мамы",
            "для маме",
            "mother's day",
            "mothers day",
        ),
    ),
    ("новый год", ("новый год", "новогод", "new year")),
    ("цветы для интерьера", ("интерьер", "в вазу", "для дома")),
    ("скайлофт", ("скайлофт", "sky loft", "skyloft")),
    ("флау вау", ("флау вау", "флаувай", "flowwow", "flow wow")),
)

_COMMENT_KEYS = (
    "description",
    "Комментарий",
    "комментарий",
    "_comment_blob",
)


def _as_float(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _as_int(value: Any) -> int:
    try:
        if value is None or value == "":
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _row_text_blob(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in _COMMENT_KEYS:
        val = row.get(key)
        if val:
            parts.append(str(val))
    for order in row.get("_orders_context") or []:
        if not isinstance(order, dict):
            continue
        for key in ("Комментарий", "description", "name"):
            if order.get(key):
                parts.append(str(order[key]))
    return " ".join(parts).lower().replace("ё", "е")


def _event_groups_from_orders(row: dict[str, Any]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for order in row.get("_orders_context") or []:
        if not isinstance(order, dict):
            continue
        month = order.get("_month") or order.get("month")
        if month is None:
            moment = str(order.get("Дата") or order.get("moment") or "")
            # ISO-ish: 2024-03-08… or 08.03.2024
            m = re.search(r"(?:^|[^0-9])(0?[1-9]|1[0-2])(?:[.\-/])", moment)
            if not m:
                m = re.search(r"-(\d{2})-", moment)
            if m:
                try:
                    month = int(m.group(1))
                except ValueError:
                    month = None
        try:
            month_i = int(month) if month is not None else 0
        except (TypeError, ValueError):
            month_i = 0
        label = _MONTH_INDEX_TO_EVENT.get(month_i)
        if label and label not in seen:
            seen.add(label)
            found.append(label)
    # March orders often align with 8 марта campaigns
    if "событие марта" in seen and "8 марта" not in seen:
        blob = _row_text_blob(row)
        if "8 марта" in blob or "8марта" in blob:
            found.append("8 марта")
    return found


def heuristic_groups_for_row(row: dict[str, Any]) -> list[str]:
    """Propose MoySklad group tags from avg check, order count, channel, keywords."""
    parts: list[str] = []
    avg = _as_float(row.get("Средний чек") or row.get("avg_check"))
    if avg >= 20000:
        parts.append("премиум")
    elif avg >= 10000:
        parts.append("букет от 10 000")

    orders = _as_int(row.get("Всего заказов") or row.get("order_count"))
    if orders >= 3:
        parts.append("постоянный клиент")
    elif orders <= 1:
        parts.append("новый")

    # Sales-type tags come from order channels only — MoySklad group tags
    # like «премиум» must not be treated as marketplace channels.
    order_channels: list[str] = []
    for order in row.get("_orders_context") or []:
        if not isinstance(order, dict):
            continue
        ch = order.get("Канал продаж")
        if ch and str(ch).strip():
            order_channels.append(str(ch).strip())
    if not order_channels:
        stored = str(row.get("Канал продаж") or "").strip()
        if stored:
            order_channels = [stored]
    sales_type = sales_channel_type_from_channels(order_channels)
    if sales_type == SALES_CHANNEL_TYPE_MARKETPLACE:
        parts.append("маркетплейс")
    elif sales_type == SALES_CHANNEL_TYPE_DIRECT and order_channels:
        parts.append("прямые продажи")

    blob = _row_text_blob(row)
    for tag, keywords in _KEYWORD_GROUPS:
        if any(k in blob for k in keywords):
            parts.append(tag)

    parts.extend(_event_groups_from_orders(row))

    # Dedupe preserving order (case-insensitive)
    seen: set[str] = set()
    out: list[str] = []
    for tag in parts:
        key = tag.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(tag.strip())
    return out


def merge_tags(existing: list[str] | None, proposed: list[str] | None) -> list[str]:
    """Union existing MoySklad tags with proposed groups (no duplicates)."""
    seen: set[str] = set()
    out: list[str] = []
    for tag in list(existing or []) + list(proposed or []):
        name = str(tag).strip()
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def propose_groups_for_rows(
    rows: list[dict[str, Any]],
    *,
    counterparty_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Build assign preview payloads for matching rows."""
    id_filter = {str(i).strip() for i in (counterparty_ids or []) if str(i).strip()}
    results: list[dict[str, Any]] = []
    for row in rows:
        cp_id = str(row.get("_moysklad_id") or row.get("id") or "").strip()
        if not cp_id:
            continue
        if id_filter and cp_id not in id_filter:
            continue
        existing = list(row.get("_moysklad_tags") or moysklad_group_tokens(row))
        proposed = heuristic_groups_for_row(row)
        merged = merge_tags(existing, proposed)
        added = [t for t in merged if t.lower() not in {e.lower() for e in existing}]
        results.append({
            "id": cp_id,
            "name": row.get("Наименование") or row.get("name") or "",
            "existing": existing,
            "proposed": proposed,
            "added": added,
            "merged": merged,
            "changed": bool(added),
        })
    return results


def push_merged_tags(
    client: MoySkladClient,
    assignments: list[dict[str, Any]],
    *,
    only_changed: bool = True,
) -> dict[str, Any]:
    """Apply merged tag lists to MoySklad counterparties."""
    pushed: list[dict[str, Any]] = []
    skipped = 0
    errors: list[dict[str, Any]] = []
    for item in assignments:
        if only_changed and not item.get("changed"):
            skipped += 1
            continue
        cp_id = str(item.get("id") or "").strip()
        tags = list(item.get("merged") or [])
        if not cp_id or not tags:
            skipped += 1
            continue
        try:
            result = client.push_tags(cp_id, tags)
            pushed.append(result)
        except Exception as exc:  # noqa: BLE001 — surface per-row failures
            errors.append({"id": cp_id, "error": str(exc)})
    return {
        "ok": not errors,
        "pushed": len(pushed),
        "skipped": skipped,
        "errors": errors,
        "results": pushed,
    }
