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
    assert stats["inactive"] == 0
    assert tg_verify.overlay_for_client("c1")["active"] is True
    # A miss is not «нет TG» — New Contact can still see the number.
    assert not tg_verify.overlay_for_client("c2")
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


def test_phone_miss_with_nick_stays_unchecked(monkeypatch):
    """Privacy can hide a number while the @nick resolves — a phone miss must
    NOT hard-mark a nick-carrying client as «не найден» (prod false negatives:
    operator finds the number manually in Telegram)."""
    from plugins.moysklad import tg_verify
    from plugins.platforms.telegram_user import client as tg_user

    rows = [
        {
            "_moysklad_id": "cn-1",
            "Наименование": "С ником",
            "Телефон": "+7 900 111-22-33",
            "ТГ ник": "@hidden_by_privacy",
        },
        {
            "_moysklad_id": "cn-2",
            "Наименование": "Только телефон",
            "Телефон": "+7 900 222-33-44",
        },
    ]
    monkeypatch.setattr(
        tg_user,
        "resolve_phones_bulk",
        lambda phones: {
            "ok": True,
            "requested": len(phones),
            "checked": list(phones),
            "found": {},
            "flood_wait": 0,
        },
    )
    tg_verify.verify_rows_by_phone_bulk(rows)
    # Neither miss is a hard «нет» — privacy hides numbers that New Contact sees.
    assert not tg_verify.overlay_for_client("cn-1")
    assert not tg_verify.overlay_for_client("cn-2")


def test_live_thread_marks_client_active(tmp_path, monkeypatch):
    from plugins.moysklad import tg_verify
    from plugins.moysklad.conversations import append_message, clear_memory_for_tests

    clear_memory_for_tests()
    append_message(
        client_id="ch-1",
        text="сиски",
        direction="inbound",
        tg_nick="@pawels2137",
        tg_chat_id="796461007",
        client_name="Hans",
        source="telegram_user",
    )
    rows = [{"_moysklad_id": "ch-1", "Наименование": "Hans", "Телефон": ""}]
    marked = tg_verify.mark_active_from_threads(rows)
    assert marked == 1
    entry = tg_verify.overlay_for_client("ch-1")
    assert entry["active"] is True
    assert entry["via"] == "history"


def test_reset_inactive_keeps_actives(monkeypatch):
    from plugins.moysklad import tg_verify

    tg_verify.save_verify_results_bulk(
        {
            "ok-1": {"active": True, "via": "import_contacts_bulk"},
            "bad-1": {"active": False, "via": "import_contacts_bulk"},
            "bad-2": {"active": False, "via": "import_contacts_bulk"},
        }
    )
    dropped = tg_verify.reset_inactive_entries()
    assert dropped == 2
    assert tg_verify.overlay_for_client("ok-1")["active"] is True
    assert not tg_verify.overlay_for_client("bad-1")
