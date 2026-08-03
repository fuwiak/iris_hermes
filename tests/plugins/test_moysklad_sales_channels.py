"""Unit tests for MoySklad CRM tab classification (no live API)."""

from __future__ import annotations

from plugins.moysklad.sales_channels import (
    is_direct_sales_channel,
    is_marketplace_channel,
    row_matches_direct_audience,
    row_matches_marketplace_audience,
    sales_channel_type_from_channels,
)


def test_direct_sales_channels() -> None:
    assert is_direct_sales_channel("Telegram")
    assert is_direct_sales_channel("WhatsApp/MAX")
    assert is_direct_sales_channel("Витрина")
    assert is_direct_sales_channel("сайт vereskflowers.ru")
    assert is_direct_sales_channel("watsapp")


def test_marketplace_channels() -> None:
    assert is_marketplace_channel("FlowWow Floday")
    assert is_marketplace_channel("Ozon")
    assert is_marketplace_channel("Wildberries")
    assert not is_marketplace_channel("Telegram")


def test_sales_channel_type_marketplace_wins() -> None:
    assert (
        sales_channel_type_from_channels(["Telegram", "FlowWow Floday"])
        == "маркетплейс"
    )
    assert sales_channel_type_from_channels(["Витрина", "WhatsApp"]) == "прямые продажи"


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
