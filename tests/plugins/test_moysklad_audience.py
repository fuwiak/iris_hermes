"""Audience extras: group_source + days_before_event window."""

from __future__ import annotations

from datetime import date

from plugins.moysklad.audience import (
    event_dates_for_row,
    normalize_group_source,
    row_matches_audience_extras,
    row_matches_days_before_event,
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
