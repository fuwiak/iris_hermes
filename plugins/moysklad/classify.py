"""Build CRM-style client lists filtered by Маркетплейс / Прямые / groups."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

from plugins.moysklad.audience import (
    normalize_group_source,
    normalize_stage_filter,
    row_matches_audience_extras,
    stage_counts,
    stamp_row_event_index,
)
from plugins.moysklad.catalog_cache import ensure_audience_ready
from plugins.moysklad.client import MoySkladClient
from plugins.moysklad.dedupe import (
    dedupe_catalog_rows,
    dedupe_entity_pages,
    recompute_audience_counts,
)
from plugins.moysklad.groups import (
    collect_featured_group_counts,
    ensure_group_options_by_source,
    split_group_options_by_source,
    stamp_row_group_index,
)
from plugins.moysklad.order_status import (
    classify_order_payment,
    client_stage,
    client_stage_reason,
    summarize_order_context,
)
from plugins.moysklad.sales_channels import (
    SALES_CHANNEL_TYPE_HYBRID,
    counterparty_row_from_api,
    entity_ref_id,
    format_channels_display,
    is_marketplace_channel,
    refresh_row_channel_fields,
    resolve_channel_name,
    row_audience_bucket,
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


def _enrich_counterparty_row(
    cp: dict[str, Any],
    *,
    channels_by_agent: dict[str, list[str]],
    order_ctx_by_agent: dict[str, list[dict[str, Any]]],
    sums_by_agent: dict[str, list[float]],
    names_by_agent: dict[str, str],
) -> dict[str, Any] | None:
    """Build one CRM row from a MoySklad counterparty + order indexes."""
    cp_id = str(cp.get("id") or "").strip()
    if not cp_id:
        return None
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
        ch = str(
            (item or {}).get("Канал продаж") or (item or {}).get("channel") or ""
        ).strip()
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

    payment = summarize_order_context(ctx if isinstance(ctx, list) else [])
    amounts = sums_by_agent.get(cp_id) or []
    if not amounts and payment.get("avg_check_paid") is not None:
        amounts = [float(payment["avg_check_paid"])]
    # Display totals include all API orders; fulfilled = paid only.
    order_count = int(payment.get("order_count") or 0)
    fulfilled = int(payment.get("fulfilled_order_count") or 0)
    avg_check = payment.get("avg_check_paid")
    if avg_check is None and amounts:
        avg_check = round(sum(amounts) / len(amounts), 2)
    last_order = (
        payment.get("last_paid_order_at") or payment.get("last_order_at") or ""
    )
    row["Всего заказов"] = order_count
    row["Средний чек"] = avg_check if avg_check is not None else ""
    row["Дата последнего заказа"] = last_order or ""
    row["order_count"] = order_count
    row["fulfilled_order_count"] = fulfilled
    row["paid_order_count"] = int(payment.get("paid_order_count") or 0)
    row["unpaid_order_count"] = int(payment.get("unpaid_order_count") or 0)
    row["cancelled_order_count"] = int(payment.get("cancelled_order_count") or 0)
    row["failed_only"] = bool(payment.get("failed_only"))
    row["customer_outcome"] = payment.get("customer_outcome") or "none"
    row["Тип клиента"] = client_stage(row["customer_outcome"])
    row["client_stage"] = row["Тип клиента"]
    row["client_stage_reason"] = client_stage_reason(payment)
    row["avg_check"] = avg_check
    row["last_order_at"] = last_order or None
    refresh_row_channel_fields(row)
    if not row.get("Наименование") and cp_id in names_by_agent:
        row["Наименование"] = names_by_agent[cp_id]
    desc = cp.get("description")
    if desc:
        row["description"] = str(desc)
        row["_comment_blob"] = str(desc)
    return row


def build_enriched_catalog(
    client: MoySkladClient,
    *,
    max_orders: int = 25000,
    max_counterparties: int = 0,
    include_archived: bool = False,
    on_partial: Callable[[dict[str, Any]], None] | None = None,
    flush_every: int = 500,
) -> dict[str, Any]:
    """Fetch MoySklad counterparties + orders into CRM-shaped rows.

    When ``on_partial`` is set, flushes a ``partial=True`` catalog every
    ``flush_every`` counterparties (and after each API page) so Redis/file
    can serve scroll/load-more while the rest of the download continues.
    """
    channels_payload = client.channels(fetch_all=True, limit=0, include_archived=True)
    channels_by_id = sales_channels_by_id(list(channels_payload.get("rows") or []))

    orders_payload = client.orders(fetch_all=True, limit=max_orders)
    # Stage-1 safety on raw API pages (overlapping offsets / retries).
    orders = dedupe_entity_pages(orders_payload.get("rows") or [])

    def _fetch_channel(channel_id: str) -> dict[str, Any] | None:
        try:
            return client.get_sales_channel(channel_id)
        except Exception:
            return None

    channels_by_agent: dict[str, list[str]] = defaultdict(list)
    order_ctx_by_agent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    sums_by_agent: dict[str, list[float]] = defaultdict(list)
    names_by_agent: dict[str, str] = {}

    for order in orders:
        agent_id = _agent_id_from_order(order)
        if not agent_id:
            continue
        # Resolve even when saleschannel is archived (list omit + GET by id).
        ch = resolve_channel_name(
            order, channels_by_id, fetch_channel=_fetch_channel
        )
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
        applicable = order.get("applicable")
        ctx_item = {
            "id": str(order.get("id") or "").strip(),
            "Канал продаж": ch or "",
            "channel": ch or "",
            "Сумма": amount,
            "sum": amount,
            "payed_sum": payed if order.get("payedSum") is not None else None,
            "unpaid": unpaid,
            "state": state_name,
            "applicable": applicable if isinstance(applicable, bool) else None,
            "Дата": order.get("moment"),
            "moment": order.get("moment"),
            "description": desc,
            "name": oname,
            "product_snippet": snippet,
            "_month": month,
        }
        ctx_item["payment_status"] = classify_order_payment(ctx_item)
        order_ctx_by_agent[agent_id].append(ctx_item)
        # Avg check only from paid orders — failed checkouts must not inflate.
        if amount > 0 and ctx_item["payment_status"] == "paid":
            sums_by_agent[agent_id].append(amount)
        name = _agent_name_from_order(order)
        if name and agent_id not in names_by_agent:
            names_by_agent[agent_id] = name

    # Page counterparties so we can flush Redis/file before the full download ends.
    from plugins.moysklad.client import PAGE_SIZE as _CP_PAGE

    rows: list[dict[str, Any]] = []
    counterparties_scanned = 0
    seen_cp_ids: set[str] = set()
    cp_offset = 0
    last_flush_at = 0
    flush_n = max(100, int(flush_every or 500))
    unlimited = max_counterparties <= 0

    def _flush_partial() -> None:
        nonlocal last_flush_at
        if on_partial is None:
            return
        # Cheap path: flush raw rows so far; final return still dedupes fully.
        partial_rows = dedupe_catalog_rows(list(rows))
        on_partial(
            {
                "rows": partial_rows,
                "counts": recompute_audience_counts(partial_rows),
                "orders_scanned": len(orders),
                "counterparties_scanned": counterparties_scanned,
                "counterparties_deduped": len(partial_rows),
                "partial": True,
            }
        )
        last_flush_at = len(rows)

    while unlimited or counterparties_scanned < max_counterparties:
        batch_limit = _CP_PAGE if unlimited else min(
            _CP_PAGE, max_counterparties - counterparties_scanned
        )
        if batch_limit <= 0:
            break
        cps_payload = client.counterparties(
            fetch_all=False,
            limit=batch_limit,
            offset=cp_offset,
            include_archived=include_archived,
        )
        batch = dedupe_entity_pages(cps_payload.get("rows") or [])
        if not batch:
            break
        for cp in batch:
            cp_id = str(cp.get("id") or "").strip()
            if cp_id:
                if cp_id in seen_cp_ids:
                    continue
                seen_cp_ids.add(cp_id)
            row = _enrich_counterparty_row(
                cp,
                channels_by_agent=channels_by_agent,
                order_ctx_by_agent=order_ctx_by_agent,
                sums_by_agent=sums_by_agent,
                names_by_agent=names_by_agent,
            )
            if row is None:
                continue
            # Stamp event/group indexes at build time (cost hides behind API
            # delays) so partial flushes and the final catalog are served
            # filter-ready — no O(n) restamp walk inside /clients requests.
            stamp_row_event_index(row)
            stamp_row_group_index(row)
            rows.append(row)
            counterparties_scanned += 1
            if (
                on_partial is not None
                and len(rows) - last_flush_at >= flush_n
            ):
                _flush_partial()
            if not unlimited and counterparties_scanned >= max_counterparties:
                break
        if len(batch) < batch_limit:
            break
        cp_offset += len(batch)

    if on_partial is not None and rows and len(rows) != last_flush_at:
        _flush_partial()

    # Multi-stage dedupe (id → contact → fuzzy); counts after collapse.
    rows = dedupe_catalog_rows(rows)
    counts = recompute_audience_counts(rows)

    return {
        "rows": rows,
        "counts": counts,
        "orders_scanned": len(orders),
        "counterparties_scanned": counterparties_scanned,
        "counterparties_deduped": len(rows),
        "partial": False,
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
            row.get("Канал продаж"),
            " ".join(str(c) for c in (row.get("_order_channels_all") or [])),
        )
    ).lower()
    if needle in blob:
        return True
    # Phone search: normalize both sides so "+7 (919) …" matches "7919…" / "919…".
    from plugins.moysklad.dedupe import normalize_phone

    needle_phone = normalize_phone(needle)
    if needle_phone:
        row_phone = normalize_phone(row.get("Телефон") or "")
        if row_phone and (
            needle_phone in row_phone
            or row_phone in needle_phone
            or row_phone.endswith(needle_phone)
            or needle_phone.endswith(row_phone)
        ):
            return True
    return False


def _public_client(row: dict[str, Any]) -> dict[str, Any]:
    # Always recompute from order context — stale cache may store first-only channel.
    refresh_row_channel_fields(row)
    channels = unique_sales_channels(row)
    audience = row.get("_audience") or {}
    sales_type = (
        str(row.get("Тип канала продаж") or "").strip()
        or sales_channel_type_from_channels(channels)
    )
    channel_display = format_channels_display(channels)
    # Prefer live aggregates from order context when present.
    ctx = row.get("_orders_context") or []
    raw_avg = row.get("avg_check")
    if raw_avg is None or raw_avg == "":
        raw_avg = row.get("Средний чек")
    try:
        avg_check: float | None = (
            float(raw_avg) if raw_avg not in (None, "") else None
        )
    except (TypeError, ValueError):
        avg_check = None
    order_count = int(row.get("order_count") or row.get("Всего заказов") or 0)
    last_order_at = (
        row.get("last_order_at") or row.get("Дата последнего заказа") or None
    )
    if last_order_at == "":
        last_order_at = None
    payment = summarize_order_context(ctx if isinstance(ctx, list) else [])
    if isinstance(ctx, list) and ctx:
        order_count = int(payment.get("order_count") or len(ctx))
        avg_check = payment.get("avg_check_paid")
        if avg_check is None and raw_avg not in (None, ""):
            try:
                avg_check = float(raw_avg)
            except (TypeError, ValueError):
                avg_check = None
        last_order_at = (
            payment.get("last_paid_order_at")
            or payment.get("last_order_at")
            or last_order_at
        )
    elif not ctx:
        # No order context and no stored count → keep zeros / nulls honest.
        if not order_count:
            avg_check = None
            last_order_at = None
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
        "fulfilled_order_count": int(
            row.get("fulfilled_order_count")
            or payment.get("fulfilled_order_count")
            or 0
        ),
        "paid_order_count": int(
            row.get("paid_order_count") or payment.get("paid_order_count") or 0
        ),
        "unpaid_order_count": int(
            row.get("unpaid_order_count") or payment.get("unpaid_order_count") or 0
        ),
        "cancelled_order_count": int(
            row.get("cancelled_order_count")
            or payment.get("cancelled_order_count")
            or 0
        ),
        "failed_only": bool(row.get("failed_only") or payment.get("failed_only")),
        "customer_outcome": (
            row.get("customer_outcome")
            or payment.get("customer_outcome")
            or "none"
        ),
        "avg_check": avg_check,
        "last_order_at": last_order_at,
        # Recomputed here too: catalogs cached before «Тип клиента» existed.
        "client_stage": row.get("client_stage")
        or client_stage(
            row.get("customer_outcome") or payment.get("customer_outcome") or "none"
        ),
        "client_stage_reason": row.get("client_stage_reason")
        or client_stage_reason(payment or row),
        "birthdate": row.get("Дата рождения") or row.get("birthdate") or "",
        "bonus_points": row.get("Баллы начисленные") or "",
        "role": row.get("Заказчик или получатель") or "",
        "actual_address": row.get("Фактический адрес") or "",
        "actual_address_comment": row.get("Фактический адрес (Комментарий)") or "",
        "company_type": row.get("Тип контрагента") or "",
        "sex": row.get("Пол") or "",
        "tg_nick": row.get("ТГ ник") or "",
        "tg_active": row.get("tg_active"),
        "tg_active_label": row.get("tg_active_label") or "",
        "tg_active_nick": row.get("tg_active_nick") or "",
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
    max_orders: int = 25000,
    max_counterparties: int = 0,
    include_archived: bool = False,
    catalog: dict[str, Any] | None = None,
    channel_kind: str = "",
    require_phone: bool = False,
    require_telegram: bool = False,
    vip_only: bool = False,
    birthday_soon: bool = False,
    group_source: str = "any",
    days_before_event: int = 0,
    event_date_from: str = "",
    event_date_to: str = "",
    stage: str = "all",
    entity_type: str = "all",
) -> dict[str, Any]:
    """Dashboard /clients payload: filtered rows + group chip cloud.

    Extra audience knobs (channel_kind / require_* / vip / birthday /
    group_source / days_before_event) share the same deduped catalog as the
    Clients table — ``matched_total`` is post-dedupe.
    """
    filter_key = _normalize_filter_key(sales_filter)
    catalog = catalog or build_enriched_catalog(
        client,
        max_orders=max_orders,
        max_counterparties=max_counterparties,
        include_archived=include_archived,
    )
    all_rows: list[dict[str, Any]] = list(catalog["rows"])
    try:
        from plugins.moysklad.tg_verify import stamp_catalog_rows_from_verify

        stamp_catalog_rows_from_verify(all_rows)
    except Exception:
        pass
    # Stamp counts + event indexes once per catalog version — do NOT rewrite
    # every channel field on each filter click (that made calendar feel frozen).
    counts = ensure_audience_ready(catalog)

    # Search spans all sales tabs — otherwise marketplace clients vanish under
    # default «Прямые» and the UI looks like broken search/filters.
    q_active = bool((q or "").strip())
    effective_filter = "all" if q_active else filter_key
    base_rows = [
        r
        for r in all_rows
        if row_matches_sales_filter(r, effective_filter) and _row_matches_query(r, q)
    ]
    group_options = collect_featured_group_counts(
        base_rows, sales_filter=filter_key, selected=group or ""
    )
    group_options_by_source = split_group_options_by_source(group_options)
    src = normalize_group_source(group_source)
    try:
        days_window = int(days_before_event or 0)
    except (TypeError, ValueError):
        days_window = 0
    stage_key = normalize_stage_filter(stage)
    ev_from = str(event_date_from or "").strip()
    ev_to = str(event_date_to or "").strip()
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
            group_source=src,
            days_before_event=days_window,
            event_date_from=ev_from,
            event_date_to=ev_to,
            stage=stage_key,
            entity_type=entity_type,
        )
    ]

    limit = max(0, min(int(limit), 500))
    offset = max(0, int(offset))
    page = matched[offset : offset + limit] if limit else matched[offset:]
    matched_total = len(matched)
    returned = len(page)
    next_offset = offset + returned
    has_more = next_offset < matched_total

    payload = {
        "ok": True,
        "sales_filter": filter_key,
        "group": group or "",
        "group_source": src,
        "q": q or "",
        "channel_kind": (channel_kind or "").strip().lower() or "any",
        "require_phone": bool(require_phone),
        "require_telegram": bool(require_telegram),
        "vip_only": bool(vip_only),
        "birthday_soon": bool(birthday_soon),
        "days_before_event": days_window,
        "event_date_from": ev_from,
        "event_date_to": ev_to,
        "stage": stage_key,
        "entity_type": (entity_type or "all").strip().lower() or "all",
        "stage_counts": stage_counts(base_rows),
        "counts": counts,
        "groups_total": len(base_rows),
        "matched_total": matched_total,
        "returned": returned,
        "offset": offset,
        "limit": limit,
        "next_offset": next_offset,
        "has_more": has_more,
        "group_options": group_options,
        "group_options_by_source": group_options_by_source,
        "clients": [_public_client(r) for r in page],
        "orders_scanned": catalog.get("orders_scanned", 0),
        "counterparties_scanned": catalog.get("counterparties_scanned", 0),
        "counterparties_deduped": catalog.get("counterparties_deduped", len(all_rows)),
        "_rows": matched,  # internal for assign (same filter)
        "_base_rows": base_rows,
        "_all_rows": all_rows,
    }
    return ensure_group_options_by_source(payload)


def catalog_integrity(catalog: dict[str, Any]) -> dict[str, Any]:
    """Explain tab counts vs hybrid/no-orders/marker-only breakdowns.

    Historic «lost ~3000» came from non-exclusive marketplace∪direct filters.
    Current tabs are an exclusive partition: ``direct + marketplace == total``.
    """
    rows: list[dict[str, Any]] = list(catalog.get("rows") or [])
    counts = catalog.get("counts") or recompute_audience_counts(rows)
    hybrid_type = 0
    no_orders = 0
    marker_only_mp = 0
    mp_by_channel = 0
    for row in rows:
        channels = unique_sales_channels(row)
        sales_type = sales_channel_type_from_channels(channels)
        if sales_type == SALES_CHANNEL_TYPE_HYBRID:
            hybrid_type += 1
        ctx = row.get("_orders_context") or []
        if not ctx and not int(row.get("order_count") or row.get("Всего заказов") or 0):
            no_orders += 1
        bucket = row_audience_bucket(row)
        has_mp_channel = any(is_marketplace_channel(c) for c in channels)
        if bucket == "marketplace":
            if has_mp_channel:
                mp_by_channel += 1
            else:
                marker_only_mp += 1
    total = int(counts.get("total") or len(rows))
    direct = int(counts.get("direct") or 0)
    marketplace = int(counts.get("marketplace") or 0)
    other = int(counts.get("other") or 0)
    return {
        "ok": True,
        "total": total,
        "direct": direct,
        "marketplace": marketplace,
        "other": other,
        "sum_tabs": direct + marketplace,
        "partition_ok": direct + marketplace == total and other == 0,
        "hybrid_type": hybrid_type,
        "no_orders": no_orders,
        "marketplace_by_channel": mp_by_channel,
        "marketplace_marker_only": marker_only_mp,
        "orders_scanned": int(catalog.get("orders_scanned") or 0),
        "counterparties_scanned": int(catalog.get("counterparties_scanned") or 0),
        "counterparties_deduped": int(
            catalog.get("counterparties_deduped") or len(rows)
        ),
        "note": (
            "Вкладки exclusive: hybrid и marker-only считаются в Маркетплейс. "
            "Колонка «Тип канала» может быть hybrid, даже если вкладка = marketplace. "
            "Старое расхождение total≠mp+direct — от non-exclusive фильтров."
        ),
    }


def clients_by_sales_type(
    client: MoySkladClient,
    *,
    sales_filter: str = "all",
    limit: int = 50,
    max_orders: int = 25000,
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
