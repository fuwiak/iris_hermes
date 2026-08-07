"""Build CRM-style client lists filtered by Маркетплейс / Прямые / groups."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from plugins.moysklad.audience import row_matches_audience_extras
from plugins.moysklad.catalog_cache import refresh_audience_counts
from plugins.moysklad.client import MoySkladClient
from plugins.moysklad.dedupe import (
    dedupe_catalog_rows,
    dedupe_entity_pages,
    recompute_audience_counts,
)
from plugins.moysklad.groups import collect_featured_group_counts
from plugins.moysklad.sales_channels import (
    channel_name_from_order,
    counterparty_row_from_api,
    entity_ref_id,
    refresh_row_channel_fields,
    row_matches_sales_filter,
    sales_channel_type_from_channels,
    sales_channels_by_id,
    unique_sales_channels,
)


def _agent_id_from_order(order: dict[str, Any]) -> str | None:
    agent = order.get("agent")
    return entity_ref_id(agent)


def _agent_name_from_order(order: dict[str, Any]) -> str:
    agent = order.get("agent")
    if isinstance(agent, dict):
        return str(agent.get("name") or "").strip()
    return ""


def _minor_to_rub(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        if isinstance(value, dict):
            value = value.get("sum", value.get("value", 0))
        return float(value) / 100.0
    except (TypeError, ValueError):
        return 0.0


def _order_month(order: dict[str, Any]) -> int | None:
    moment = str(order.get("moment") or order.get("deliveryPlannedMoment") or "")
    if len(moment) >= 7 and moment[4] == "-":
        try:
            return int(moment[5:7])
        except ValueError:
            return None
    return None


def _normalize_filter_key(sales_filter: str) -> str:
    filter_key = (sales_filter or "all").strip().lower()
    if filter_key in ("прямые", "прямые продажи"):
        return "direct"
    if filter_key in ("маркетплейс", "маркетплейсы", "mp"):
        return "marketplace"
    return filter_key or "all"


def build_enriched_catalog(
    client: MoySkladClient,
    *,
    max_orders: int = 5000,
    max_counterparties: int = 0,
    include_archived: bool = False,
) -> dict[str, Any]:
    """Fetch MoySklad counterparties + orders into CRM-shaped rows."""
    channels_payload = client.channels(fetch_all=True, limit=0)
    channels_by_id = sales_channels_by_id(list(channels_payload.get("rows") or []))

    orders_payload = client.orders(fetch_all=True, limit=max_orders)
    # Stage-1 safety on raw API pages (overlapping offsets / retries).
    orders = dedupe_entity_pages(orders_payload.get("rows") or [])

    channels_by_agent: dict[str, list[str]] = defaultdict(list)
    order_ctx_by_agent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    sums_by_agent: dict[str, list[float]] = defaultdict(list)
    names_by_agent: dict[str, str] = {}

    for order in orders:
        agent_id = _agent_id_from_order(order)
        if not agent_id:
            continue
        ch = channel_name_from_order(order, channels_by_id)
        if ch:
            channels_by_agent[agent_id].append(ch)
        amount = _minor_to_rub(order.get("sum"))
        payed = _minor_to_rub(order.get("payedSum"))
        # Unpaid only when payedSum is present on the API row (never invent debt).
        unpaid = None
        if order.get("payedSum") is not None and order.get("payedSum") != "":
            unpaid = round(max(0.0, amount - payed), 2)
        month = _order_month(order)
        desc = str(order.get("description") or "").strip()
        oname = str(order.get("name") or "").strip()
        snippet = (desc or oname)[:120]
        state = order.get("state")
        state_name = ""
        if isinstance(state, dict):
            state_name = str(state.get("name") or "").strip()
        order_ctx_by_agent[agent_id].append({
            "id": str(order.get("id") or "").strip(),
            "Канал продаж": ch or "",
            "channel": ch or "",
            "Сумма": amount,
            "sum": amount,
            "payed_sum": payed if order.get("payedSum") is not None else None,
            "unpaid": unpaid,
            "state": state_name,
            "Дата": order.get("moment"),
            "moment": order.get("moment"),
            "description": desc,
            "name": oname,
            "product_snippet": snippet,
            "_month": month,
        })
        if amount > 0:
            sums_by_agent[agent_id].append(amount)
        name = _agent_name_from_order(order)
        if name and agent_id not in names_by_agent:
            names_by_agent[agent_id] = name

    cps_payload = client.counterparties(
        fetch_all=True,
        limit=max_counterparties,
        include_archived=include_archived,
    )
    counterparties = dedupe_entity_pages(cps_payload.get("rows") or [])

    rows: list[dict[str, Any]] = []

    for cp in counterparties:
        cp_id = str(cp.get("id") or "").strip()
        if not cp_id:
            continue
        row = counterparty_row_from_api(
            cp, order_channels=channels_by_agent.get(cp_id, [])
        )
        # Prefer full order context (with dates/sums) over channel-only stubs.
        ctx = order_ctx_by_agent.get(cp_id) or row.get("_orders_context") or []
        row["_orders_context"] = ctx
        # Deduped channel list from real orders (preserve first-seen order).
        order_channels: list[str] = []
        seen_ch: set[str] = set()
        for item in ctx:
            ch = str((item or {}).get("Канал продаж") or (item or {}).get("channel") or "").strip()
            key = ch.lower()
            if ch and key not in seen_ch:
                seen_ch.add(key)
                order_channels.append(ch)
        if not order_channels:
            order_channels = list(channels_by_agent.get(cp_id) or [])
        row["_order_channels_all"] = order_channels
        row["Канал продаж"] = ", ".join(order_channels)
        sales_type = sales_channel_type_from_channels(order_channels)
        row["Тип канала продаж"] = sales_type
        row["Тип продаж"] = sales_type

        amounts = sums_by_agent.get(cp_id) or []
        if not amounts:
            for item in ctx:
                amt = float((item or {}).get("sum") or (item or {}).get("Сумма") or 0)
                if amt > 0:
                    amounts.append(amt)
        order_count = len(ctx) if ctx else len(amounts)
        avg_check = (sum(amounts) / len(amounts)) if amounts else 0.0
        last_order = ""
        for item in ctx:
            moment = str(
                (item or {}).get("moment")
                or (item or {}).get("Дата")
                or ""
            ).strip()
            if moment and moment > last_order:
                last_order = moment
        row["Всего заказов"] = order_count
        row["Средний чек"] = round(avg_check, 2)
        row["Дата последнего заказа"] = last_order
        row["order_count"] = order_count
        row["avg_check"] = round(avg_check, 2)
        row["last_order_at"] = last_order
        refresh_row_channel_fields(row)
        if not row.get("Наименование") and cp_id in names_by_agent:
            row["Наименование"] = names_by_agent[cp_id]
        desc = cp.get("description")
        if desc:
            row["description"] = str(desc)
            row["_comment_blob"] = str(desc)
        rows.append(row)

    # Multi-stage dedupe (id → contact → fuzzy); counts after collapse.
    rows = dedupe_catalog_rows(rows)
    counts = recompute_audience_counts(rows)

    return {
        "rows": rows,
        "counts": counts,
        "orders_scanned": len(orders),
        "counterparties_scanned": len(counterparties),
        "counterparties_deduped": len(rows),
    }


def _row_matches_query(row: dict[str, Any], q: str) -> bool:
    needle = (q or "").strip().lower()
    if not needle:
        return True
    blob = " ".join(
        str(x or "")
        for x in (
            row.get("Наименование"),
            row.get("Телефон"),
            row.get("email") or row.get("E-mail"),
            row.get("_moysklad_tags_display"),
            row.get("Статус контрагента") or row.get("Статус"),
            row.get("ТГ ник"),
            row.get("TG conversation"),
            row.get("Фактический адрес"),
        )
    ).lower()
    return needle in blob


def _public_client(row: dict[str, Any]) -> dict[str, Any]:
    # Always recompute from order context — stale cache may store first-only channel.
    refresh_row_channel_fields(row)
    channels = unique_sales_channels(row)
    audience = row.get("_audience") or {}
    sales_type = sales_channel_type_from_channels(channels)
    channel_display = ", ".join(channels)
    # Prefer live aggregates from order context when present.
    ctx = row.get("_orders_context") or []
    order_count = int(row.get("order_count") or row.get("Всего заказов") or 0)
    avg_check = float(row.get("avg_check") or row.get("Средний чек") or 0)
    last_order_at = (
        row.get("last_order_at") or row.get("Дата последнего заказа") or ""
    )
    if isinstance(ctx, list) and ctx:
        amounts: list[float] = []
        last = ""
        for item in ctx:
            if not isinstance(item, dict):
                continue
            try:
                amount = float(item.get("sum") or item.get("Сумма") or 0)
            except (TypeError, ValueError):
                amount = 0.0
            if amount > 0:
                amounts.append(amount)
            moment = str(item.get("moment") or item.get("Дата") or "").strip()
            if moment and moment > last:
                last = moment
        order_count = len(ctx)
        if amounts:
            avg_check = round(sum(amounts) / len(amounts), 2)
        if last:
            last_order_at = last
    ms_tags = [str(t).strip() for t in (row.get("_moysklad_tags") or []) if str(t).strip()]
    ms_groups = (
        str(row.get("Группы") or "").strip()
        or ", ".join(ms_tags)
    )
    public = {
        "id": row.get("_moysklad_id") or "",
        "name": row.get("Наименование") or "",
        "phone": row.get("Телефон") or "",
        "email": row.get("email") or row.get("E-mail") or "",
        "state": row.get("_moysklad_state") or row.get("Статус") or "",
        "tags": ms_tags,
        "groups": ms_groups,
        "ms_groups": ms_groups,
        "channels": channels,
        "channel": channel_display,
        "sales_type": sales_type,
        "order_count": order_count,
        "avg_check": avg_check,
        "last_order_at": last_order_at,
        "bonus_points": row.get("Баллы начисленные") or "",
        "role": row.get("Заказчик или получатель") or "",
        "actual_address": row.get("Фактический адрес") or "",
        "actual_address_comment": row.get("Фактический адрес (Комментарий)") or "",
        "company_type": row.get("Тип контрагента") or "",
        "sex": row.get("Пол") or "",
        "tg_nick": row.get("ТГ ник") or "",
        "tg_conversation": row.get("TG conversation") or "",
        "balance": row.get("balance"),
        "audience": {
            "direct": bool(audience.get("direct")),
            "marketplace": bool(audience.get("marketplace")),
        },
    }
    try:
        from plugins.moysklad.ai_fill import apply_ai_fill_to_public

        return apply_ai_fill_to_public(public)
    except Exception:
        public.setdefault("ai_fields", [])
        public.setdefault("ai_groups", [])
        return public


def clients_page(
    client: MoySkladClient,
    *,
    sales_filter: str = "all",
    group: str = "",
    q: str = "",
    limit: int = 50,
    offset: int = 0,
    max_orders: int = 5000,
    max_counterparties: int = 0,
    include_archived: bool = False,
    catalog: dict[str, Any] | None = None,
    channel_kind: str = "",
    require_phone: bool = False,
    require_telegram: bool = False,
    vip_only: bool = False,
    birthday_soon: bool = False,
) -> dict[str, Any]:
    """Dashboard /clients payload: filtered rows + group chip cloud.

    Extra audience knobs (channel_kind / require_* / vip / birthday) share the
    same deduped catalog as the Clients table — ``matched_total`` is post-dedupe.
    """
    filter_key = _normalize_filter_key(sales_filter)
    catalog = catalog or build_enriched_catalog(
        client,
        max_orders=max_orders,
        max_counterparties=max_counterparties,
        include_archived=include_archived,
    )
    all_rows: list[dict[str, Any]] = list(catalog["rows"])
    # Always recompute — durable cache may still hold pre-partition counts.
    counts = refresh_audience_counts(catalog)

    base_rows = [
        r
        for r in all_rows
        if row_matches_sales_filter(r, filter_key) and _row_matches_query(r, q)
    ]
    group_options = collect_featured_group_counts(
        base_rows, sales_filter=filter_key, selected=group or ""
    )
    matched = [
        r
        for r in base_rows
        if row_matches_audience_extras(
            r,
            channel_kind=channel_kind,
            require_phone=require_phone,
            require_telegram=require_telegram,
            vip_only=vip_only,
            birthday_soon=birthday_soon,
            group=group,
        )
    ]

    limit = max(0, min(int(limit), 500))
    offset = max(0, int(offset))
    page = matched[offset : offset + limit] if limit else matched[offset:]
    matched_total = len(matched)
    returned = len(page)
    next_offset = offset + returned
    has_more = next_offset < matched_total

    return {
        "ok": True,
        "sales_filter": filter_key,
        "group": group or "",
        "q": q or "",
        "channel_kind": (channel_kind or "").strip().lower() or "any",
        "require_phone": bool(require_phone),
        "require_telegram": bool(require_telegram),
        "vip_only": bool(vip_only),
        "birthday_soon": bool(birthday_soon),
        "counts": counts,
        "groups_total": len(base_rows),
        "matched_total": matched_total,
        "returned": returned,
        "offset": offset,
        "limit": limit,
        "next_offset": next_offset,
        "has_more": has_more,
        "group_options": group_options,
        "clients": [_public_client(r) for r in page],
        "orders_scanned": catalog.get("orders_scanned", 0),
        "counterparties_scanned": catalog.get("counterparties_scanned", 0),
        "counterparties_deduped": catalog.get("counterparties_deduped", len(all_rows)),
        "_rows": matched,  # internal for assign (same filter)
        "_base_rows": base_rows,
        "_all_rows": all_rows,
    }


def clients_by_sales_type(
    client: MoySkladClient,
    *,
    sales_filter: str = "all",
    limit: int = 50,
    max_orders: int = 5000,
    max_counterparties: int = 0,
    include_archived: bool = False,
) -> dict[str, Any]:
    """Fetch MoySklad data and apply Iris CRM tab filters.

    sales_filter: ``direct`` | ``marketplace`` | ``all``
    (also accepts Russian labels «прямые» / «маркетплейс»).
    """
    page = clients_page(
        client,
        sales_filter=sales_filter,
        limit=limit,
        offset=0,
        max_orders=max_orders,
        max_counterparties=max_counterparties,
        include_archived=include_archived,
    )
    truncated = page["matched_total"] > page["returned"] if page["limit"] else False
    return {
        "ok": True,
        "sales_filter": page["sales_filter"],
        "rules": "iris_crm_tabs",
        "counts": page["counts"],
        "matched_total": page["matched_total"],
        "returned": page["returned"],
        "truncated": truncated,
        "orders_scanned": page["orders_scanned"],
        "counterparties_scanned": page["counterparties_scanned"],
        "clients": page["clients"],
        "hint": (
            "Exclusive tabs: marketplace = non-direct order channels ∪ FlowWow/"
            "status/group markers; direct = everyone else. "
            "Invariant: direct + marketplace == all."
        ),
    }
