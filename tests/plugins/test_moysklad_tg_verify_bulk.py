"""Batch phone probing for «TG активен» (importContacts per chunk)."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_overlay(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("MOYSKLAD_REDIS_URL", raising=False)
    monkeypatch.setenv("MOYSKLAD_API_TOKEN", "test-token")
    from plugins.moysklad import tg_verify

    tg_verify._MEMORY = None
    tg_verify._MEMORY_FP = None
    yield
    tg_verify._MEMORY = None
    tg_verify._MEMORY_FP = None


ROWS = [
    {"_moysklad_id": "c1", "Наименование": "На TG", "Телефон": "+7 977 575-80-58"},
    {"_moysklad_id": "c2", "Наименование": "Нет TG", "Телефон": "+7 982 235-21-88"},
    {"_moysklad_id": "c3", "Наименование": "Без телефона", "ТГ ник": "@nick"},
]


def test_bulk_probe_marks_active_and_inactive_in_one_request(monkeypatch):
    from plugins.moysklad import tg_verify
    from plugins.platforms.telegram_user import client as tg_user

    calls: list[list[str]] = []

    def _fake_bulk(phones):
        calls.append(list(phones))
        return {
            "ok": True,
            "requested": len(phones),
            "checked": list(phones),
            "found": {
                "+79775758058": {
                    "phone": "+79775758058",
                    "tg_chat_id": "777",
                    "tg_nick": "ontg",
                    "name": "На TG",
                }
            },
            "flood_wait": 0,
        }

    monkeypatch.setattr(tg_user, "resolve_phones_bulk", _fake_bulk)

    stats = tg_verify.verify_rows_by_phone_bulk(ROWS)
    # One batched request for both phones — not one per client.
    assert len(calls) == 1
    assert stats["active"] == 1
    assert stats["inactive"] == 1
    assert tg_verify.overlay_for_client("c1")["active"] is True
    assert tg_verify.overlay_for_client("c2")["active"] is False
    # Row without a phone is untouched (nick path handles it).
    assert not tg_verify.overlay_for_client("c3")


def test_flood_wait_leaves_unchecked_rows_unchecked(monkeypatch):
    """A flood wait is not proof «нет в Telegram» — do not write inactive."""
    from plugins.moysklad import tg_verify
    from plugins.platforms.telegram_user import client as tg_user

    monkeypatch.setattr(
        tg_user,
        "resolve_phones_bulk",
        lambda phones: {
            "ok": True,
            "requested": len(phones),
            "checked": [],
            "found": {},
            "flood_wait": 300,
        },
    )

    stats = tg_verify.verify_rows_by_phone_bulk(ROWS)
    assert stats["flood_wait"] == 300
    assert stats["checked"] == 0
    assert not tg_verify.overlay_for_client("c1")
    assert not tg_verify.overlay_for_client("c2")
