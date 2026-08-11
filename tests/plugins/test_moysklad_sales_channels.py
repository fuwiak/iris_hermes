"""Unit tests for MoySklad CRM tab classification (no live API)."""

from __future__ import annotations

from plugins.moysklad.dedupe import recompute_audience_counts
from plugins.moysklad.sales_channels import (
    is_direct_sales_channel,
    is_marketplace_channel,
    row_audience_bucket,
    row_matches_direct_audience,
    row_matches_marketplace_audience,
    sales_channel_type_from_channels,
    unique_sales_channels,
)


def test_direct_sales_channels() -> None:
    assert is_direct_sales_channel("Telegram")
    assert is_direct_sales_channel("WhatsApp/MAX")
    assert is_direct_sales_channel("Витрина")
    assert is_direct_sales_channel("сайт vereskflowers.ru")
    assert is_direct_sales_channel("watsapp")


def test_marketplace_channels() -> None:
    assert is_marketplace_channel("FlowWow Floday")
    assert is_marketplace_channel("FlowWow Skyloft")
    assert is_marketplace_channel("Ozon")
    assert is_marketplace_channel("Wildberries")
    assert not is_marketplace_channel("Telegram")


def test_flowwow_skyloft_marketplace_audience() -> None:
    row = {
        "_orders_context": [{"Канал продаж": "FlowWow Skyloft"}],
        "_moysklad_tags": [],
    }
    assert row_matches_marketplace_audience(row) is True
    assert row_matches_direct_audience(row) is False
    assert sales_channel_type_from_channels(["FlowWow Skyloft"]) == "маркетплейс"


def test_channel_name_from_order_archived_lookup() -> None:
    from plugins.moysklad.sales_channels import channel_name_from_order, resolve_channel_name

    order = {
        "salesChannel": {
            "meta": {"href": "https://api.moysklad.ru/api/remap/1.2/entity/saleschannel/abc-1"}
        }
    }
    assert channel_name_from_order(order, {"abc-1": "FlowWow Skyloft"}) == "FlowWow Skyloft"
    # Linked id without directory entry → None (not «Без канала»); GET fills it.
    assert channel_name_from_order(order, {}) is None
    assert (
        resolve_channel_name(
            order,
            {},
            fetch_channel=lambda _cid: {"name": "FlowWow Skyloft", "archived": True},
        )
        == "FlowWow Skyloft"
    )


def test_sales_channel_type_marketplace_wins() -> None:
    assert (
        sales_channel_type_from_channels(["Telegram", "FlowWow Floday"])
        == "маркетплейс/прямые продажи"
    )
    assert sales_channel_type_from_channels(["Витрина", "WhatsApp"]) == "прямые продажи"
    assert sales_channel_type_from_channels(["Ozon"]) == "маркетплейс"


def test_sales_channel_type_hybrid_label() -> None:
    assert (
        sales_channel_type_from_channels(["Витрина", "Ozon"])
        == "маркетплейс/прямые продажи"
    )


def test_unique_sales_channels_lists_all() -> None:
    row = {
        "_orders_context": [
            {"Канал продаж": "Витрина"},
            {"Канал продаж": "Ozon"},
            {"Канал продаж": "Витрина"},
        ],
        "_moysklad_tags": [],
    }
    assert unique_sales_channels(row) == ["Витрина", "Ozon"]


def test_unique_sales_channels_ignores_group_tags() -> None:
    """Occasion tags must not become fake marketplace channels."""
    row = {
        "_orders_context": [{"Канал продаж": "Telegram"}],
        "_moysklad_tags": ["8 марта", "букет от 10 000"],
        "_moysklad_tags_display": "8 марта, букет от 10 000",
    }
    assert unique_sales_channels(row) == ["Telegram"]


def test_direct_audience_excludes_flowwow_hybrid() -> None:
    row = {
        "_orders_context": [
            {"Канал продаж": "Витрина"},
            {"Канал продаж": "FlowWow Floday"},
        ],
        "_moysklad_tags": [],
    }
    assert row_matches_direct_audience(row) is False
    assert row_matches_marketplace_audience(row) is True


def test_direct_audience_whatsapp_only() -> None:
    row = {
        "_orders_context": [{"Канал продаж": "WhatsApp/MAX"}],
        "_moysklad_tags": ["watsapp"],
    }
    assert row_matches_direct_audience(row) is True
    assert row_matches_marketplace_audience(row) is False


def test_marketplace_audience_by_status() -> None:
    row = {
        "_orders_context": [],
        "_moysklad_state": "новый",
        "_moysklad_tags": [],
    }
    assert row_matches_marketplace_audience(row) is True
    assert row_matches_direct_audience(row) is False


def test_ozon_order_is_marketplace_not_other() -> None:
    row = {
        "_orders_context": [{"Канал продаж": "Ozon"}],
        "_moysklad_tags": [],
    }
    assert row_audience_bucket(row) == "marketplace"
    assert row_matches_direct_audience(row) is False


def test_no_channel_defaults_to_direct() -> None:
    row = {"_orders_context": [], "_moysklad_tags": [], "_moysklad_state": ""}
    assert row_audience_bucket(row) == "direct"


def test_marketplace_group_wins_over_direct_channel() -> None:
    """Exclusive: occasion group → marketplace even with Telegram orders."""
    row = {
        "_orders_context": [{"Канал продаж": "Telegram"}],
        "_moysklad_tags": ["8 марта"],
        "_moysklad_tags_display": "8 марта",
    }
    assert row_audience_bucket(row) == "marketplace"
    assert row_matches_direct_audience(row) is False


def test_audience_counts_partition_sums_to_total() -> None:
    rows = [
        {
            "_moysklad_id": "d1",
            "_orders_context": [{"Канал продаж": "Telegram"}],
            "_moysklad_tags": [],
        },
        {
            "_moysklad_id": "m1",
            "_orders_context": [{"Канал продаж": "FlowWow Floday"}],
            "_moysklad_tags": [],
        },
        {
            "_moysklad_id": "m2",
            "_orders_context": [],
            "_moysklad_state": "новый",
            "_moysklad_tags": [],
        },
        {
            "_moysklad_id": "d2",
            "_orders_context": [],
            "_moysklad_tags": ["постоянный клиент"],
            "_moysklad_tags_display": "постоянный клиент",
        },
        {
            "_moysklad_id": "m3",
            "_orders_context": [{"Канал продаж": "Витрина"}],
            "_moysklad_tags": ["8 марта"],
            "_moysklad_tags_display": "8 марта",
        },
    ]
    counts = recompute_audience_counts(rows)
    assert counts["total"] == 5
    assert counts["other"] == 0
    assert counts["direct"] + counts["marketplace"] == counts["total"]
    assert counts["direct"] == 2
    assert counts["marketplace"] == 3
    # No client in both buckets
    for row in rows:
        aud = row["_audience"]
        assert aud["direct"] != aud["marketplace"]
