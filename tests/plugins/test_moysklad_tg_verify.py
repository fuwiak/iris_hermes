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


def test_match_catalog_phones_to_contacts(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    from plugins.platforms.telegram_user import client as tu
    from plugins.moysklad.tg_verify import match_catalog_phones_to_contacts, row_tg_active

    monkeypatch.setattr(
        tu,
        "cached_contacts",
        lambda: [
            {
                "id": "u1",
                "tg_chat_id": "111",
                "tg_nick": "oknick",
                "phone": "+7 900 111-22-33",
            }
        ],
    )
    rows = [
        {"id": "c1", "Телефон": "89001112233", "name": "A"},
        {"id": "c2", "Телефон": "+79009998877", "name": "B"},
        {"id": "c3", "name": "no phone"},
    ]
    stats = match_catalog_phones_to_contacts(rows)
    assert stats["matched"] == 1
    assert stats["scanned"] == 2
    assert row_tg_active({"id": "c1"}) is True
    assert row_tg_active({"id": "c2"}) is None


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
    assert tg_active_label(active=True, has_contact=True) == "есть TG"


def test_verify_probes_second_phone_via_tme_link(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from plugins.moysklad import tg_verify
    from plugins.platforms.telegram_user import client as tg_user

    calls: list[str] = []

    def _resolve(query: str) -> dict:
        calls.append(str(query))
        if str(query) == "+79250553485":
            return {
                "ok": True,
                "tg_chat_id": "555001",
                "tg_nick": "",
                "resolved_via": "tme_phone_link",
            }
        return {"ok": False, "error": "phone_not_confirmed", "detail": "miss"}

    monkeypatch.setattr(tg_user, "is_authorized", lambda: True)
    monkeypatch.setattr(tg_user, "resolve_peer", _resolve)
    result = tg_verify.verify_client_peers(
        phone="+7 999 111-22-33, +7 925 055 3485"
    )
    assert "+79991112233" in calls
    assert "+79250553485" in calls
    assert result["active"] is True
    assert result["chat_id"] == "555001"
    assert result["via"] == "tme_phone_link"


def test_row_tg_active_prefers_overlay_over_stale_row(monkeypatch, tmp_path) -> None:
    """Catalog/localStorage can bake wrong tg_active; overlay is source of truth."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    from plugins.moysklad.tg_verify import row_tg_active, save_verify_result

    save_verify_result(
        "c-overlay",
        {"active": True, "via": "irbots", "detail": "активный (есть сессия TG)"},
    )
    stale = {"id": "c-overlay", "tg_active": False, "Телефон": "+79001112233"}
    assert row_tg_active(stale) is True


def test_stamp_uses_irbots_detail_as_label(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    from plugins.moysklad.tg_verify import save_verify_result, stamp_catalog_rows_from_verify

    save_verify_result(
        "c-label",
        {
            "active": False,
            "via": "irbots",
            "detail": "неактивный (не зарегистрирован)",
        },
    )
    row = {"id": "c-label", "Телефон": "+79001112233"}
    assert stamp_catalog_rows_from_verify([row]) == 1
    assert row["tg_active"] is False
    assert row["tg_active_label"] == "неактивный (не зарегистрирован)"


def test_match_catalog_hits_second_number_in_cell(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    from plugins.platforms.telegram_user import client as tu
    from plugins.moysklad.tg_verify import match_catalog_phones_to_contacts, row_tg_active

    monkeypatch.setattr(
        tu,
        "cached_contacts",
        lambda: [
            {
                "id": "u1",
                "tg_chat_id": "555001",
                "tg_nick": "",
                "phone": "+7 925 055 3485",
            }
        ],
    )
    rows = [
        {
            "id": "c1",
            "Телефон": "+7 900 111-22-33, +7 925 055 3485",
            "name": "A",
        }
    ]
    stats = match_catalog_phones_to_contacts(rows)
    assert stats["matched"] == 1
    assert row_tg_active({"id": "c1"}) is True
