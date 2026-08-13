"""Bulk personal-TG dialog sync → «TG conversation» column threads."""

from __future__ import annotations

from pathlib import Path

import pytest

from plugins.moysklad import conversations as conv
from plugins.platforms.telegram_user import client as tg_user


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("MOYSKLAD_REDIS_URL", raising=False)
    monkeypatch.setenv("MOYSKLAD_API_TOKEN", "test-token-abc")
    conv.clear_memory_for_tests()
    conv.clear_dialog_sync_backoff_for_tests()
    yield
    conv.clear_memory_for_tests()
    conv.clear_dialog_sync_backoff_for_tests()


ROWS = [
    {"_moysklad_id": "ms-1", "Наименование": "Петр", "ТГ ник": "@petr", "Телефон": ""},
    {"_moysklad_id": "ms-2", "Наименование": "Анна", "Телефон": "+7 999 123-45-67"},
    {"_moysklad_id": "ms-3", "Наименование": "Без ТГ", "Телефон": ""},
]

CONTACTS = [
    {"id": "111", "tg_chat_id": "111", "tg_nick": "petr", "name": "Petr", "phone": "", "peer_source": "dialog"},
    {"id": "222", "tg_chat_id": "222", "tg_nick": "", "name": "Anna", "phone": "79991234567", "peer_source": "contact"},
    {"id": "333", "tg_chat_id": "333", "tg_nick": "stranger", "name": "Not a client", "phone": "", "peer_source": "dialog"},
]


def _fake_sync_ok(**kw):
    thread = conv.append_message(
        client_id=kw["client_id"],
        text=f"привет от {kw['client_name']}",
        direction="inbound",
        channel="telegram",
        phone=kw.get("phone", ""),
        tg_nick=kw.get("tg_nick", ""),
        client_name=kw.get("client_name", ""),
        source="telegram_user",
    )
    thread["sync"] = {"ok": True, "imported": 1, "inbound_imported": 1}
    return thread


def test_matches_by_nick_and_phone_and_writes_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tg_user, "cached_contacts", lambda: CONTACTS)
    monkeypatch.setattr(conv, "sync_from_telegram_user", _fake_sync_ok)
    monkeypatch.setattr(conv.time, "sleep", lambda *_: None)

    stats = conv.sync_telegram_dialogs_into_threads(ROWS)
    assert stats["ok"] is True
    assert stats["matched"] == 2  # petr by nick, anna by phone; stranger no match
    assert stats["synced"] == 2
    assert stats["inbound_imported"] == 2

    thread = conv.get_thread(client_id="ms-1")
    assert thread["message_count"] == 1
    assert "привет" in (thread["preview"] or "")


def test_fresh_threads_are_skipped_on_second_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tg_user, "cached_contacts", lambda: CONTACTS)
    monkeypatch.setattr(conv, "sync_from_telegram_user", _fake_sync_ok)
    monkeypatch.setattr(conv.time, "sleep", lambda *_: None)

    first = conv.sync_telegram_dialogs_into_threads(ROWS)
    assert first["synced"] == 2

    second = conv.sync_telegram_dialogs_into_threads(ROWS)
    assert second["attempted"] == 0
    assert second["skipped_fresh"] == 2


def test_failed_peer_backs_off(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _fake_sync_fail(**kw):
        calls.append(kw["client_id"])
        return {"sync": {"ok": False, "error": "privacy"}, "message_count": 0}

    monkeypatch.setattr(tg_user, "cached_contacts", lambda: CONTACTS)
    monkeypatch.setattr(conv, "sync_from_telegram_user", _fake_sync_fail)
    monkeypatch.setattr(conv.time, "sleep", lambda *_: None)

    first = conv.sync_telegram_dialogs_into_threads(ROWS)
    assert first["errors"] == 2
    second = conv.sync_telegram_dialogs_into_threads(ROWS)
    assert second["attempted"] == 0
    assert calls.count("ms-1") == 1


def test_no_cached_contacts_reports_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tg_user, "cached_contacts", lambda: [])
    stats = conv.sync_telegram_dialogs_into_threads(ROWS)
    assert stats["ok"] is False
    assert stats["error"] == "no_cached_contacts"


def test_max_peers_caps_work_and_reports_backlog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tg_user, "cached_contacts", lambda: CONTACTS)
    monkeypatch.setattr(conv, "sync_from_telegram_user", _fake_sync_ok)
    monkeypatch.setattr(conv.time, "sleep", lambda *_: None)

    stats = conv.sync_telegram_dialogs_into_threads(ROWS, max_peers=1)
    assert stats["attempted"] == 1
    assert stats["pending_left"] == 1


def test_hot_thread_resyncs_before_bulk_min_age(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fresh traffic (outreach awaiting an answer) re-syncs on the next pass;
    quiet threads keep the 6h bulk cadence."""
    monkeypatch.setattr(tg_user, "cached_contacts", lambda: CONTACTS)
    monkeypatch.setattr(conv, "sync_from_telegram_user", _fake_sync_ok)
    monkeypatch.setattr(conv.time, "sleep", lambda *_: None)

    first = conv.sync_telegram_dialogs_into_threads(ROWS)
    assert first["synced"] == 2

    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    stale_stamp = conv.time.time() - 3600  # 1h ago — inside the 6h window
    with conv._LOCK:
        store = conv._load()
        for tid, thread in store["threads"].items():
            thread["dialog_synced_at"] = stale_stamp
            hours_ago = 100 if tid == "ms-2" else 0  # ms-2 quiet, ms-1 hot
            ts = (now - timedelta(hours=hours_ago, minutes=5)).isoformat()
            for m in thread["messages"]:
                m["ts"] = ts
        conv._save(store)

    second = conv.sync_telegram_dialogs_into_threads(ROWS)
    assert second["attempted"] == 1
    assert second["synced"] == 1
    hot = conv.get_thread(client_id="ms-1")
    assert hot["message_count"] == 2  # re-sync merged the new inbound
