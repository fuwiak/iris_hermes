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
    row_has_group,
)


def test_crm_featured_groups_include_events_and_bouquet() -> None:
    all_groups = crm_featured_groups("all")
    assert "8 марта" in all_groups
    assert "букет от 10 000" in all_groups or "букет от 10000" in all_groups
    assert "событие марта" in all_groups
    assert "событие март" in all_groups

    direct = crm_featured_groups("direct")
    assert "8 марта" in direct
    assert "букет от 10 000" not in direct


def test_row_has_group_and_counts() -> None:
    rows = [
        {
            "_moysklad_tags": ["8 марта", "Telegram"],
            "_moysklad_tags_display": "8 марта, Telegram",
        },
        {
            "_moysklad_tags": ["событие июля", "букет от 10 000"],
            "Группы": "событие июля, букет от 10 000",
        },
        {"_moysklad_tags": []},
    ]
    assert row_has_group(rows[0], "8 марта") is True
    assert row_has_group(rows[0], "новый год") is False

    cloud = collect_featured_group_counts(rows, sales_filter="all")
    names = {item["name"]: item["count"] for item in cloud}
    assert names.get("8 марта") == 1
    assert names.get("событие июля") == 1
    assert names.get("букет от 10 000") == 1


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
