"""Telegram reachability overlay + audience filter sync."""

from __future__ import annotations

from plugins.moysklad.audience import row_matches_audience_extras, row_passes_telegram_filter
from plugins.moysklad.tg_verify import (
    row_tg_active,
    save_verify_result,
    stamp_catalog_rows_from_verify,
    tg_active_label,
)


def test_row_passes_telegram_filter_strict() -> None:
    assert row_passes_telegram_filter({"tg_active": True, "tg_nick": "@a"}) is True
    assert row_passes_telegram_filter({"tg_active": False, "tg_nick": "@a"}) is False
    assert row_passes_telegram_filter({"tg_nick": "@a"}) is False


def test_require_telegram_audience_extras_uses_verified_only() -> None:
    active = {"tg_active": True, "tg_nick": "@ok", "_moysklad_tags": []}
    dead = {"tg_active": False, "tg_nick": "@dead", "_moysklad_tags": []}
    unchecked = {"tg_nick": "@maybe", "_moysklad_tags": []}
    assert row_matches_audience_extras(active, require_telegram=True) is True
    assert row_matches_audience_extras(dead, require_telegram=True) is False
    assert row_matches_audience_extras(unchecked, require_telegram=True) is False


def test_stamp_catalog_rows_from_verify(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    cid = "cp-test-1"
    save_verify_result(
        cid,
        {
            "active": True,
            "resolved_nick": "@fresh",
            "chat_id": "123",
            "via": "mtproto",
            "detail": "",
        },
    )
    row = {"id": cid, "tg_nick": "@stale", "Телефон": "+79001112233"}
    assert stamp_catalog_rows_from_verify([row]) == 1
    assert row["tg_active"] is True
    assert row["tg_active_nick"] == "fresh"
    assert row_tg_active(row) is True
    assert tg_active_label(active=True, has_contact=True) == "активен"
