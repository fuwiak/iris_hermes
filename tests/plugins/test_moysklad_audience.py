"""Audience extras: group_source + days_before_event window."""

from __future__ import annotations

from datetime import date

from plugins.moysklad.audience import (
    event_dates_for_row,
    normalize_group_source,
    parse_event_date,
    row_matches_audience_extras,
    row_matches_days_before_event,
    row_matches_event_calendar,
)
from plugins.moysklad.classify import catalog_integrity
from plugins.moysklad.groups import row_has_group, split_group_options_by_source


def test_normalize_group_source() -> None:
    assert normalize_group_source("МойСклад") == "ms"
    assert normalize_group_source("ИИ") == "ai"
    assert normalize_group_source("") == "any"


def test_row_has_group_source_scoped(monkeypatch) -> None:
    from plugins.moysklad import groups as groups_mod

    monkeypatch.setattr(
        groups_mod,
        "row_ai_groups",
        lambda row: ["премиум"] if row.get("_moysklad_id") == "1" else [],
    )
    row = {
        "_moysklad_id": "1",
        "_moysklad_tags": ["8 марта"],
    }
    assert row_has_group(row, "8 марта", source="ms") is True
    assert row_has_group(row, "8 марта", source="ai") is False
    assert row_has_group(row, "премиум", source="ai") is True
    assert row_has_group(row, "премиум", source="ms") is False


def test_event_calendar_matches_literal_order_day() -> None:
    """Seller picking the order day (not only 8 Mar / mid-month) must find client."""
    today = date(2026, 8, 12)
    # Same shape as card: March order + «событие марта» (mid-month proxy = 15).
    row = {
        "_moysklad_id": "viktor",
        "_moysklad_tags": ["событие марта", "флаувау"],
        "_orders_context": [
            {
                "moment": "2026-03-01 09:55:00",
                "sum": 5732,
                "_month": 3,
                "channel": "Flow Wow Сокольники",
            }
        ],
    }
    dates = event_dates_for_row(row, today=today)
    assert date(2026, 3, 1) in dates
    assert row_matches_event_calendar(
        row,
        event_from=date(2026, 3, 1),
        event_to=date(2026, 3, 1),
        lead_days=0,
        today=today,
    ) is True
    # Soft proxies still work.
    assert row_matches_event_calendar(
        row,
        event_from=date(2026, 3, 8),
        event_to=date(2026, 3, 8),
        lead_days=0,
        today=today,
    ) is True
    assert row_matches_event_calendar(
        row,
        event_from=date(2026, 3, 15),
        event_to=date(2026, 3, 15),
        lead_days=0,
        today=today,
    ) is True
    assert row_matches_event_calendar(
        row,
        event_from=date(2026, 4, 1),
        event_to=date(2026, 4, 1),
        lead_days=0,
        today=today,
    ) is False


def test_event_calendar_matches_last_order_at_without_context_moment() -> None:
    """Stub channel-only context + last_order_at must still match calendar day."""
    today = date(2026, 8, 12)
    row = {
        "_moysklad_id": "viktor-stub",
        "_orders_context": [{"Канал продаж": "Flow Wow Сокольники"}],
        "last_order_at": "2026-03-01 09:55",
        "Дата последнего заказа": "2026-03-01 09:55",
    }
    assert row_matches_event_calendar(
        row,
        event_from=date(2026, 3, 1),
        event_to=date(2026, 3, 1),
        lead_days=0,
        today=today,
    ) is True


def test_event_calendar_matches_order_day_anniversary() -> None:
    """Order on 2025-03-01 must match seller picking 2026-03-01."""
    today = date(2026, 8, 12)
    row = {
        "_moysklad_id": "viktor-anniv",
        "_orders_context": [
            {"moment": "2025-03-01 09:55:00", "sum": 5732, "_month": 3}
        ],
    }
    assert row_matches_event_calendar(
        row,
        event_from=date(2026, 3, 1),
        event_to=date(2026, 3, 1),
        lead_days=0,
        today=today,
    ) is True


def test_parse_event_date_moysklad_and_ru_formats() -> None:
    assert parse_event_date("2026-03-01 09:55:00") == date(2026, 3, 1)
    assert parse_event_date("01.03.2026") == date(2026, 3, 1)
    assert parse_event_date("2026-03-01T09:55:00") == date(2026, 3, 1)


def test_stamped_event_index_matches_calendar_without_reparsing_orders() -> None:
    from plugins.moysklad.audience import stamp_row_event_index

    row = {
        "_moysklad_id": "viktor",
        "_orders_context": [
            {"moment": "2026-03-01 09:55:00", "sum": 5732, "_month": 3}
        ],
        "last_order_at": "2026-03-01 09:55",
    }
    stamp_row_event_index(row)
    assert "2026-03-01" in (row["_event_index_v1"]["literals"] or [])
    # Drop raw orders — index alone must still match.
    row["_orders_context"] = []
    row.pop("last_order_at", None)
    assert row_matches_event_calendar(
        row,
        event_from=date(2026, 3, 1),
        event_to=date(2026, 3, 1),
        lead_days=0,
        today=date(2026, 8, 13),
    ) is True


def test_event_calendar_august_tag_and_order_season() -> None:
    """August calendar pick must match «событие августа» and Aug order history."""
    today = date(2026, 8, 12)
    tagged = {"_moysklad_tags": ["событие августа"], "_moysklad_id": "aug-tag"}
    from_orders = {
        "_moysklad_id": "aug-ord",
        "_moysklad_tags": [],
        "_orders_context": [
            {"moment": "2025-08-18 12:00:00", "sum": 3000, "_month": 8},
        ],
    }
    birthday = {
        "_moysklad_id": "aug-bd",
        "_moysklad_tags": [],
        "Дата рождения": "1990-08-15",
    }
    other = {
        "_moysklad_id": "mar",
        "_moysklad_tags": ["8 марта"],
        "_orders_context": [],
    }

    # Single day Aug 15
    for row in (tagged, from_orders, birthday):
        assert row_matches_event_calendar(
            row,
            event_from=date(2026, 8, 15),
            event_to=date(2026, 8, 15),
            lead_days=0,
            today=today,
        ) is True
    assert row_matches_event_calendar(
        other,
        event_from=date(2026, 8, 15),
        event_to=date(2026, 8, 15),
        lead_days=0,
        today=today,
    ) is False

    # Range 10–20 Aug (what seller picks on the Aug 2026 calendar)
    for row in (tagged, from_orders, birthday):
        assert row_matches_event_calendar(
            row,
            event_from=date(2026, 8, 10),
            event_to=date(2026, 8, 20),
            lead_days=0,
            today=today,
        ) is True
    assert row_matches_event_calendar(
        other,
        event_from=date(2026, 8, 10),
        event_to=date(2026, 8, 20),
        lead_days=0,
        today=today,
    ) is False


def test_event_calendar_swapped_range_and_lead() -> None:
    today = date(2026, 8, 12)
    row = {"_moysklad_tags": ["событие августа"], "_moysklad_id": "x"}
    # from > to must still match
    assert row_matches_event_calendar(
        row,
        event_from=date(2026, 8, 20),
        event_to=date(2026, 8, 10),
        lead_days=0,
        today=today,
    ) is True
    # Lead: today Aug 12 is within 5d of Aug 15
    assert row_matches_event_calendar(
        row,
        event_from=date(2026, 8, 10),
        event_to=date(2026, 8, 20),
        lead_days=5,
        today=today,
    ) is True
    assert row_matches_event_calendar(
        row,
        event_from=date(2026, 8, 10),
        event_to=date(2026, 8, 20),
        lead_days=1,
        today=today,
    ) is False


def test_clients_page_filters_multiple_clients_by_august_range() -> None:
    """Outreach /clients with event_date_* keeps only matching occasion clients."""
    from plugins.moysklad.classify import clients_page
    from plugins.moysklad.sales_channels import refresh_row_channel_fields

    rows = [
        {
            "_moysklad_id": "a",
            "Наименование": "Август",
            "Телефон": "+79001110001",
            "_moysklad_tags": ["событие августа"],
            "_orders_context": [
                {"id": "1", "Канал продаж": "Telegram", "channel": "Telegram", "sum": 1}
            ],
        },
        {
            "_moysklad_id": "b",
            "Наименование": "ДР 15 авг",
            "Телефон": "+79001110002",
            "_moysklad_tags": [],
            "Дата рождения": "1988-08-15",
            "_orders_context": [
                {"id": "2", "Канал продаж": "Витрина", "channel": "Витрина", "sum": 1}
            ],
        },
        {
            "_moysklad_id": "c",
            "Наименование": "Март",
            "Телефон": "+79001110003",
            "_moysklad_tags": ["8 марта"],
            "_orders_context": [
                {"id": "3", "Канал продаж": "Ozon", "channel": "Ozon", "sum": 1}
            ],
        },
        {
            "_moysklad_id": "d",
            "Наименование": "Без события",
            "Телефон": "+79001110004",
            "_moysklad_tags": [],
            "_orders_context": [
                {"id": "4", "Канал продаж": "Telegram", "channel": "Telegram", "sum": 1}
            ],
        },
    ]
    for r in rows:
        refresh_row_channel_fields(r)
    catalog = {
        "rows": rows,
        "counts": {"total": 4, "direct": 3, "marketplace": 1},
        "orders_scanned": 4,
        "counterparties_scanned": 4,
        "counterparties_deduped": 4,
    }

    class _Dummy:
        pass

    page = clients_page(
        _Dummy(),  # type: ignore[arg-type]
        sales_filter="all",
        event_date_from="2026-08-10",
        event_date_to="2026-08-20",
        catalog=catalog,
    )
    assert {c["id"] for c in page["clients"]} == {"a", "b"}
    assert page["matched_total"] == 2
    assert page["event_date_from"] == "2026-08-10"
    assert page["event_date_to"] == "2026-08-20"

    single = clients_page(
        _Dummy(),  # type: ignore[arg-type]
        sales_filter="all",
        event_date_from="2026-08-15",
        event_date_to="2026-08-15",
        catalog=catalog,
    )
    assert {c["id"] for c in single["clients"]} == {"a", "b"}


def test_event_calendar_single_day_no_lead() -> None:
    today = date(2026, 3, 1)
    row = {"_moysklad_tags": ["8 марта"], "_moysklad_id": "x"}
    assert row_matches_event_calendar(
        row,
        event_from=date(2026, 3, 8),
        event_to=date(2026, 3, 8),
        lead_days=0,
        today=today,
    ) is True
    assert row_matches_event_calendar(
        row,
        event_from=date(2026, 3, 9),
        event_to=date(2026, 3, 9),
        lead_days=0,
        today=today,
    ) is False


def test_event_calendar_past_range_in_august() -> None:
    """Seller picking March dates in August must still match «8 марта» tag."""
    today = date(2026, 8, 12)
    row = {"_moysklad_tags": ["8 марта"], "_moysklad_id": "x"}
    assert row_matches_event_calendar(
        row,
        event_from=date(2026, 3, 8),
        event_to=date(2026, 3, 8),
        lead_days=0,
        today=today,
    ) is True
    # Lead window requires «today inside [event-lead, event]» — August misses.
    assert row_matches_event_calendar(
        row,
        event_from=date(2026, 3, 8),
        event_to=date(2026, 3, 8),
        lead_days=5,
        today=today,
    ) is False


def test_event_calendar_range_with_lead() -> None:
    today = date(2026, 3, 4)
    row = {"_moysklad_tags": ["8 марта"], "_moysklad_id": "x"}
    assert row_matches_event_calendar(
        row,
        event_from=date(2026, 3, 1),
        event_to=date(2026, 3, 15),
        lead_days=5,
        today=today,
    ) is True
    assert row_matches_event_calendar(
        row,
        event_from=date(2026, 3, 1),
        event_to=date(2026, 3, 15),
        lead_days=3,
        today=today,
    ) is False


def test_audience_extras_event_calendar_params(monkeypatch) -> None:
    fixed = date(2026, 3, 4)
    monkeypatch.setattr(
        "plugins.moysklad.audience.date",
        type(
            "FixedDate",
            (date,),
            {"today": classmethod(lambda cls: fixed)},
        ),
    )
    row = {"_moysklad_tags": ["8 марта"], "_moysklad_id": "x"}
    assert row_matches_audience_extras(
        row,
        event_date_from="2026-03-08",
        event_date_to="2026-03-08",
        days_before_event=5,
    )
    assert not row_matches_audience_extras(
        row,
        event_date_from="2026-03-08",
        event_date_to="2026-03-08",
        days_before_event=1,
    )


def test_clients_page_event_date_range_builds_audience() -> None:
    """Outreach calendar range must filter clients by event dates."""
    from plugins.moysklad.classify import clients_page
    from plugins.moysklad.sales_channels import refresh_row_channel_fields

    hit = {
        "_moysklad_id": "e1",
        "Наименование": "С 8 марта",
        "Телефон": "+79001111111",
        "_moysklad_tags": ["8 марта"],
        "_moysklad_tags_display": "8 марта",
        "_orders_context": [
            {"id": "o1", "Канал продаж": "Telegram", "channel": "Telegram", "sum": 1000}
        ],
        "order_count": 1,
    }
    miss = {
        "_moysklad_id": "e2",
        "Наименование": "Без события",
        "Телефон": "+79002222222",
        "_moysklad_tags": [],
        "_orders_context": [
            {"id": "o2", "Канал продаж": "Ozon", "channel": "Ozon", "sum": 1000}
        ],
        "order_count": 1,
    }
    refresh_row_channel_fields(hit)
    refresh_row_channel_fields(miss)
    catalog = {
        "rows": [hit, miss],
        "counts": {"total": 2, "direct": 1, "marketplace": 1},
        "orders_scanned": 2,
        "counterparties_scanned": 2,
        "counterparties_deduped": 2,
    }

    class _Dummy:
        pass

    page = clients_page(
        _Dummy(),  # type: ignore[arg-type]
        sales_filter="all",
        event_date_from="2026-03-08",
        event_date_to="2026-03-08",
        catalog=catalog,
    )
    assert {c["id"] for c in page["clients"]} == {"e1"}
    assert page["matched_total"] == 1

    ranged = clients_page(
        _Dummy(),  # type: ignore[arg-type]
        sales_filter="all",
        event_date_from="2026-03-01",
        event_date_to="2026-03-15",
        catalog=catalog,
    )
    assert {c["id"] for c in ranged["clients"]} == {"e1"}


def test_days_before_event_8_march() -> None:
    today = date(2026, 3, 4)  # 4 days before 8 March
    row = {"_moysklad_tags": ["8 марта"], "_moysklad_id": "x"}
    assert row_matches_days_before_event(row, 5, today=today) is True
    assert row_matches_days_before_event(row, 3, today=today) is False
    dates = event_dates_for_row(row, today=today)
    assert date(2026, 3, 8) in dates


def test_days_before_event_month_bucket() -> None:
    today = date(2026, 3, 10)
    row = {"_moysklad_tags": ["событие марта"]}
    # Mid-month default = March 15 → 5 days away
    assert row_matches_days_before_event(row, 5, today=today) is True
    assert row_matches_days_before_event(row, 2, today=today) is False


def test_clients_page_group_filter_narrows_audience() -> None:
    """Group chip click must change /clients matched set (Саша: фильтры)."""
    from plugins.moysklad.classify import clients_page
    from plugins.moysklad.sales_channels import refresh_row_channel_fields

    with_group = {
        "_moysklad_id": "g1",
        "Наименование": "С группой",
        "Телефон": "+79001111111",
        "_moysklad_tags": ["8 марта"],
        "_moysklad_tags_display": "8 марта",
        "Группы": "8 марта",
        "_orders_context": [
            {"id": "o1", "Канал продаж": "Telegram", "channel": "Telegram", "sum": 1000}
        ],
        "order_count": 1,
    }
    without = {
        "_moysklad_id": "g2",
        "Наименование": "Без группы",
        "Телефон": "+79002222222",
        "_moysklad_tags": [],
        "_moysklad_tags_display": "",
        "Группы": "",
        "_orders_context": [
            {"id": "o2", "Канал продаж": "Telegram", "channel": "Telegram", "sum": 1000}
        ],
        "order_count": 1,
    }
    refresh_row_channel_fields(with_group)
    refresh_row_channel_fields(without)
    catalog = {
        "rows": [with_group, without],
        "counts": {"total": 2, "direct": 2, "marketplace": 0},
        "orders_scanned": 2,
        "counterparties_scanned": 2,
        "counterparties_deduped": 2,
    }

    class _Dummy:
        pass

    all_page = clients_page(_Dummy(), sales_filter="all", catalog=catalog)  # type: ignore[arg-type]
    assert all_page["matched_total"] == 2

    filtered = clients_page(
        _Dummy(),  # type: ignore[arg-type]
        sales_filter="all",
        group="8 марта",
        group_source="ms",
        catalog=catalog,
    )
    ids = {c["id"] for c in filtered["clients"]}
    assert ids == {"g1"}
    assert filtered["matched_total"] == 1


def test_clients_page_vip_and_phone_filters() -> None:
    from plugins.moysklad.classify import clients_page
    from plugins.moysklad.sales_channels import refresh_row_channel_fields

    vip = {
        "_moysklad_id": "v1",
        "Наименование": "VIP",
        "Телефон": "+79003333333",
        "_moysklad_tags": ["VIP"],
        "_moysklad_tags_display": "VIP",
        "_orders_context": [],
        "order_count": 0,
    }
    no_phone = {
        "_moysklad_id": "v2",
        "Наименование": "NoPhone",
        "Телефон": "",
        "_moysklad_tags": [],
        "_orders_context": [],
        "order_count": 0,
    }
    refresh_row_channel_fields(vip)
    refresh_row_channel_fields(no_phone)
    catalog = {
        "rows": [vip, no_phone],
        "counts": {"total": 2, "direct": 2, "marketplace": 0},
        "orders_scanned": 0,
        "counterparties_scanned": 2,
        "counterparties_deduped": 2,
    }

    class _Dummy:
        pass

    vip_page = clients_page(
        _Dummy(),  # type: ignore[arg-type]
        sales_filter="all",
        vip_only=True,
        catalog=catalog,
    )
    assert {c["id"] for c in vip_page["clients"]} == {"v1"}

    phone_page = clients_page(
        _Dummy(),  # type: ignore[arg-type]
        sales_filter="all",
        require_phone=True,
        catalog=catalog,
    )
    assert {c["id"] for c in phone_page["clients"]} == {"v1"}


def test_audience_extras_group_source_and_window(monkeypatch) -> None:
    from plugins.moysklad import groups as groups_mod

    monkeypatch.setattr(groups_mod, "row_ai_groups", lambda _row: ["премиум"])
    row = {
        "_moysklad_id": "c1",
        "_moysklad_tags": ["8 марта", "событие марта"],
        "_moysklad_tags_display": "8 марта, событие марта",
        "Телефон": "+79001112233",
    }
    assert row_matches_audience_extras(row, group="премиум", group_source="ai")
    assert not row_matches_audience_extras(row, group="премиум", group_source="ms")
    assert row_matches_audience_extras(row, birthday_soon=True)
    # Fixed today so window is deterministic regardless of wall clock.
    today = date(2026, 3, 4)
    assert row_matches_days_before_event(row, 5, today=today) is True
    assert row_matches_days_before_event(row, 1, today=today) is False


def test_split_group_options_by_source() -> None:
    items = [
        {"name": "8 марта", "count": 2, "ms_count": 2, "ai_count": 1, "source": "both"},
        {"name": "премиум", "count": 1, "ms_count": 0, "ai_count": 1, "source": "ai"},
    ]
    split = split_group_options_by_source(items)
    assert any(i["name"] == "8 марта" for i in split["ms"])
    assert any(i["name"] == "8 марта" for i in split["ai"])
    assert any(i["name"] == "премиум" for i in split["ai"])
    assert not any(i["name"] == "премиум" for i in split["ms"])


def test_days_before_event_soft_tag_fallback() -> None:
    """Tagged event without concrete date still matches window filter."""
    today = date(2026, 6, 1)  # far from fixed occasions
    row = {"_moysklad_tags": ["день рождения", "событие"], "_moysklad_id": "x"}
    assert row_matches_days_before_event(row, 5, today=today) is True
    assert row_matches_days_before_event(row, 5, today=today) is True


def test_days_before_from_order_season() -> None:
    today = date(2026, 3, 4)
    row = {
        "_moysklad_id": "s1",
        "_moysklad_tags": [],
        "_orders_context": [
            {"moment": "2025-03-10 12:00:00", "sum": 1000, "_month": 3},
        ],
    }
    assert row_matches_days_before_event(row, 5, today=today) is True


def test_row_ai_groups_heuristic_fallback(monkeypatch) -> None:
    from plugins.moysklad import groups as groups_mod

    monkeypatch.setattr(
        "plugins.moysklad.ai_fill.ai_group_labels_for_client",
        lambda _cid: [],
    )
    row = {
        "_moysklad_id": "h1",
        "order_count": 1,
        "avg_check": 15000,
        "_orders_context": [{"Канал продаж": "Telegram", "sum": 15000, "payment_status": "paid"}],
        "fulfilled_order_count": 1,
        "paid_order_count": 1,
    }
    tokens = groups_mod.row_ai_groups(row)
    assert tokens
    assert any("новый" in t.lower() or "премиум" in t.lower() for t in tokens)


def test_ensure_ai_featured_chips_always_in_ai_section() -> None:
    from plugins.moysklad.groups import (
        collect_featured_group_counts,
        ensure_group_options_by_source,
        split_group_options_by_source,
    )

    # MS-only tags, no AI fill store — AI section must still get soft chips.
    rows = [
        {
            "_moysklad_id": "only-ms",
            "_moysklad_tags": ["8 марта"],
            "order_count": 0,
            "_orders_context": [],
        }
    ]
    opts = collect_featured_group_counts(rows, sales_filter="direct")
    split = split_group_options_by_source(opts)
    assert any(i["name"] == "8 марта" for i in split["ms"])
    assert split["ai"], "AI chip section must not be empty"
    assert any(i["name"] == "новый" for i in split["ai"])

    # Stale snapshot with only MS options — repair path fills AI.
    repaired = ensure_group_options_by_source(
        {"group_options": [{"name": "8 марта", "count": 1, "ms_count": 1, "ai_count": 0, "source": "ms"}]}
    )
    assert repaired["group_options_by_source"]["ai"]
    assert any(i["name"] == "премиум" for i in repaired["group_options_by_source"]["ai"])


def test_catalog_integrity_partition() -> None:
    rows = [
        {
            "_moysklad_id": "d1",
            "_orders_context": [{"Канал продаж": "Telegram", "sum": 1000}],
            "_moysklad_tags": [],
            "_audience": {"direct": True, "marketplace": False},
        },
        {
            "_moysklad_id": "m1",
            "_orders_context": [
                {"Канал продаж": "Telegram", "sum": 500},
                {"Канал продаж": "Ozon", "sum": 700},
            ],
            "_moysklad_tags": [],
            "_audience": {"direct": False, "marketplace": True},
        },
        {
            "_moysklad_id": "m2",
            "_orders_context": [],
            "_moysklad_state": "новый",
            "_moysklad_tags": [],
            "_audience": {"direct": False, "marketplace": True},
        },
    ]
    from plugins.moysklad.dedupe import recompute_audience_counts

    recompute_audience_counts(rows)
    report = catalog_integrity({"rows": rows, "counts": recompute_audience_counts(rows)})
    assert report["partition_ok"] is True
    assert report["total"] == report["sum_tabs"]
    assert report["hybrid_type"] == 1
    assert report["marketplace_marker_only"] >= 1
