"""Tests for the personal-account (MTProto) Telegram client.

No Telethon and no network: everything that touches Telegram goes through
``_call`` / ``is_authorized``, which the tests stub.
"""

from __future__ import annotations

import json

import plugins.platforms.telegram_user.client as tu


def _clear_env(monkeypatch):
    for key in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_USER_SESSION"):
        monkeypatch.delenv(key, raising=False)


def test_status_without_credentials(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    status = tu.user_status()
    assert status["ok"] is True
    assert status["api_configured"] is False
    assert status["session_saved"] is False
    assert status["authorized"] is False


def test_save_credentials_rejects_non_numeric_api_id(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    out = tu.save_credentials(api_id="abc", api_hash="deadbeef")
    assert out["ok"] is False
    assert out["error"] == "api_id_invalid"


def test_save_credentials_persists_0600(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert tu.save_credentials(api_id="123456", api_hash="hash")["ok"] is True
    assert tu.api_credentials() == ("123456", "hash")
    cfg = tmp_path / "telegram_user" / "config.json"
    assert json.loads(cfg.read_text())["api_id"] == "123456"
    assert oct(cfg.stat().st_mode)[-3:] == "600"


def test_env_credentials_beat_stored(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    tu.save_credentials(api_id="1", api_hash="stored")
    monkeypatch.setenv("TELEGRAM_API_ID", "999")
    monkeypatch.setenv("TELEGRAM_API_HASH", "env")
    assert tu.api_credentials() == ("999", "env")


def test_status_masks_env_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_API_ID", "29924508")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abcdef0123456789abcdef0123456789")
    status = tu.user_status(probe=False)
    assert status["api_configured"] is True
    assert status["api_source"] == "env"
    assert status["api_id_masked"].startswith("29")
    assert "9" not in status["api_id_masked"][2:]  # digits after prefix masked
    assert "•" in status["api_id_masked"]
    assert status["api_hash_masked"]
    assert "abcdef" not in status["api_hash_masked"]
    assert set(status["api_hash_masked"]) <= {"•"}


def test_mask_secret_helpers():
    assert tu.mask_secret("") == ""
    assert tu.mask_secret("ab", keep=2) == "••"
    assert tu.mask_secret("abcd", keep=2).startswith("ab")
    assert "c" not in tu.mask_secret("abcd", keep=2)[2:]
    assert set(tu.mask_secret("secret", keep=0)) == {"•"}


def test_login_without_phone(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    out = tu.start_login(phone="")
    assert out["ok"] is False
    assert out["error"] == "phone_missing"


def test_login_without_api_credentials(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    out = tu.start_login(phone="+79991234567")
    assert out["ok"] is False
    assert out["error"] == "api_credentials_missing"


def test_is_authorized_false_without_session(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    tu.save_credentials(api_id="1", api_hash="h")
    assert tu.is_authorized(ttl=0) is False


def test_peer_arg_forms():
    assert tu._peer_arg("415321451") == 415321451
    assert tu._peer_arg("-1001234") == -1001234
    assert tu._peer_arg("https://t.me/papa2139") == "@papa2139"
    assert tu._peer_arg("tg://user?id=415321451") == 415321451
    assert tu._peer_arg("papa2139") == "@papa2139"
    assert tu._peer_arg("@papa2139") == "@papa2139"


def test_contacts_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert tu.cached_contacts() == []
    assert tu.contacts_stale() is True

    class _User:
        def __init__(self, uid, first, username, phone=""):
            self.id = uid
            self.first_name = first
            self.last_name = ""
            self.username = username
            self.phone = phone
            self.bot = False

    class _Bot(_User):
        def __init__(self):
            super().__init__(7, "helper", "helper_bot")
            self.bot = True

    monkeypatch.setattr(
        tu,
        "_call",
        lambda factory, timeout=60.0: {
            "ok": True,
            "users": [_User(415321451, "Ася", "papa2139", "79990001122"), _Bot()],
        },
    )
    out = tu.fetch_contacts(force=True)
    assert out["ok"] is True
    assert out["total"] == 1  # bots dropped
    assert out["contacts"][0]["tg_chat_id"] == "415321451"
    assert tu.contacts_stale() is False

    found = tu.find_cached_contact(tg_nick="@papa2139")
    assert found is not None and found["name"] == "Ася"
    assert tu.find_cached_contact(tg_chat_id="415321451") is not None
    assert tu.find_cached_contact(tg_nick="nobody") is None


def test_fetch_contacts_merges_private_dialogs(tmp_path, monkeypatch):
    """Saved address book is usually tiny — chats are where the people are."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    class _User:
        def __init__(self, uid, first, username=""):
            self.id = uid
            self.first_name = first
            self.last_name = ""
            self.username = username
            self.phone = ""
            self.bot = False
            self.is_self = False

    monkeypatch.setattr(
        tu,
        "_call",
        lambda factory, timeout=60.0: {
            "ok": True,
            "users": [_User(1, "Ася", "papa2139")],
            "dialog_users": [_User(1, "Ася", "papa2139"), _User(2, "Иван")],
        },
    )
    out = tu.fetch_contacts(force=True)
    assert out["total"] == 2  # deduped by id
    assert out["from_dialogs"] == 1
    assert out["from_address_book"] == 1
    assert {c["id"] for c in out["contacts"]} == {"1", "2"}


def test_fetch_contacts_falls_back_to_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    tu._write_json(
        tu._contacts_path(),
        {"fetched_at": 0, "contacts": [{"tg_chat_id": "1", "tg_nick": "a", "name": "A"}]},
    )
    monkeypatch.setattr(
        tu,
        "_call",
        lambda factory, timeout=60.0: {
            "ok": False,
            "error": "not_authorized",
            "detail": "нет сессии",
        },
    )
    out = tu.fetch_contacts(force=True)
    assert out["ok"] is False
    assert out["cached"] is True
    assert out["total"] == 1


def test_send_message_requires_text_and_peer(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert tu.send_message(peer="@x", text="  ")["error"] == "empty_text"
    assert tu.send_message(peer="", text="hi")["error"] == "telegram_chat_missing"


def test_telethon_missing_maps_to_friendly_error():
    out = tu._runtime_error(RuntimeError("telethon_missing"))
    assert out["error"] == "telethon_missing"
    out = tu._runtime_error(RuntimeError("api_credentials_missing"))
    assert out["error"] == "api_credentials_missing"
