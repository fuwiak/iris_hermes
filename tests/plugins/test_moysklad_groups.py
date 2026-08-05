"""Unit tests for MoySklad group cloud + heuristic assign (no live API)."""

from __future__ import annotations

from plugins.moysklad.assign_groups import (
    heuristic_groups_for_row,
    merge_tags,
    propose_groups_for_rows,
)
from plugins.moysklad.groups import (
    collect_featured_group_counts,
    crm_featured_groups,
    normalize_group_key,
    row_has_group,
)
from plugins.moysklad.recalculate_groups import assign_to_taxonomy, propose_taxonomy


def test_crm_featured_groups_shared_on_direct_and_marketplace() -> None:
    all_groups = crm_featured_groups("all")
    assert "8 марта" in all_groups
    assert "букет от 10 000" in all_groups
    assert "событие марта" in all_groups
    # Nominative duplicate collapsed — only genitive canonical remains.
    assert "событие март" not in all_groups

    direct = crm_featured_groups("direct")
    assert "8 марта" in direct
    assert "букет от 10 000" in direct
    assert "флау вау" in direct
    assert "скайлофт" in direct
    assert "цветы для интерьера" in direct

    mp = crm_featured_groups("marketplace")
    assert "8 марта" in mp
    assert "букет от 10 000" in mp


def test_normalize_group_aliases_and_counts() -> None:
    assert normalize_group_key("букет от 10000") == "букет от 10 000"
    assert normalize_group_key("событие март") == "событие марта"
    assert normalize_group_key("день матери") == "день мам"

    rows = [
        {
            "_moysklad_tags": ["8 марта", "Telegram"],
            "_moysklad_tags_display": "8 марта, Telegram",
        },
        {
            "_moysklad_tags": ["событие март", "букет от 10000"],
            "_moysklad_tags_display": "событие март, букет от 10000",
        },
        {"_moysklad_tags": []},
    ]
    assert row_has_group(rows[0], "8 марта") is True
    assert row_has_group(rows[1], "букет от 10 000") is True
    assert row_has_group(rows[1], "событие марта") is True

    cloud = collect_featured_group_counts(rows, sales_filter="direct")
    names = {item["name"]: item["count"] for item in cloud}
    assert names.get("8 марта") == 1
    assert names.get("событие марта") == 1
    assert names.get("букет от 10 000") == 1
    # No double-count under nominative alias
    assert "событие март" not in names


def test_heuristic_groups_avg_and_orders() -> None:
    row = {
        "Средний чек": 15000,
        "Всего заказов": 4,
        "_orders_context": [{"Канал продаж": "WhatsApp/MAX", "_month": 3}],
        "_moysklad_tags": [],
    }
    tags = heuristic_groups_for_row(row)
    assert "букет от 10 000" in tags
    assert "постоянный клиент" in tags
    assert "прямые продажи" in tags
    assert "событие марта" in tags


def test_heuristic_keywords_and_merge() -> None:
    row = {
        "avg_check": 5000,
        "order_count": 1,
        "description": "заказ на 8 марта для мамы",
        "_orders_context": [{"Канал продаж": "FlowWow Floday"}],
        "_moysklad_tags": ["витрина"],
    }
    tags = heuristic_groups_for_row(row)
    assert "8 марта" in tags
    assert "день мам" in tags
    assert "маркетплейс" in tags
    assert "новый" in tags

    merged = merge_tags(["витрина"], tags)
    assert "витрина" in merged
    assert "8 марта" in merged
    assert len(merged) == len({t.lower() for t in merged})


def test_propose_groups_only_changed() -> None:
    rows = [
        {
            "_moysklad_id": "aaa",
            "Наименование": "Alice",
            "Средний чек": 25000,
            "Всего заказов": 5,
            "_orders_context": [{"Канал продаж": "Telegram"}],
            "_moysklad_tags": ["премиум", "постоянный клиент", "прямые продажи"],
        },
        {
            "_moysklad_id": "bbb",
            "Наименование": "Bob",
            "Средний чек": 12000,
            "Всего заказов": 1,
            "_orders_context": [{"Канал продаж": "Витрина"}],
            "_moysklad_tags": [],
        },
    ]
    proposals = propose_groups_for_rows(rows)
    by_id = {p["id"]: p for p in proposals}
    assert by_id["aaa"]["changed"] is False or not by_id["aaa"]["added"]
    assert by_id["bbb"]["changed"] is True
    assert "букет от 10 000" in by_id["bbb"]["added"]


def test_recalculate_taxonomy_assign() -> None:
    rows = [
        {
            "_moysklad_id": "1",
            "Наименование": "Ann",
            "Средний чек": 12000,
            "Всего заказов": 2,
            "_orders_context": [{"Канал продаж": "Telegram", "_month": 3}],
            "_moysklad_tags": ["8 марта"],
            "_moysklad_tags_display": "8 марта",
        }
    ]
    proposal = propose_taxonomy(rows, sales_filter="direct")
    assert proposal["ok"] is True
    assert proposal["groups"]
    assignments = assign_to_taxonomy(rows, ["8 марта", "букет от 10 000", "событие марта"])
    assert assignments[0]["id"] == "1"
    assert "8 марта" in assignments[0]["merged"]
