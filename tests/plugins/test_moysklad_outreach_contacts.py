"""Tests for MoySklad outreach contact picker (Рассылки dropdown)."""

from __future__ import annotations

import json

import plugins.moysklad.outreach_contacts as oc
import plugins.moysklad.telegram_send as tg
import plugins.platforms.telegram_business.client as tb


def test_add_and_list_custom_contact(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    added = oc.add_custom_contact(
        name="Ася",
        tg_nick="papa2139",
        tg_chat_id="415321451",
        resolve=False,
    )
    assert added["id"].startswith("custom:")
    assert added["tg_nick"] == "papa2139"
    assert added["tg_chat_id"] == "415321451"

    listed = oc.list_outreach_contacts()
    assert len(listed) == 1
    assert listed[0]["label"].startswith("Ася")
    assert "@papa2139" in listed[0]["label"]

    got = oc.get_contact(added["id"])
    assert got is not None
    assert got["tg_nick"] == "papa2139"


def test_custom_contact_dedupes_by_nick(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    first = oc.add_custom_contact(name="A", tg_nick="@papa2139", resolve=False)
    second = oc.add_custom_contact(
        name="B", tg_nick="papa2139", tg_chat_id="1", resolve=False
    )
    listed = oc.list_outreach_contacts()
    assert len(listed) == 1
    assert listed[0]["id"] == second["id"]
    assert listed[0]["name"] == "B"
    assert first["id"] != second["id"]


def test_add_requires_nick_or_chat_id(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    try:
        oc.add_custom_contact(name="x", resolve=False)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "ник" in str(exc).lower() or "chat" in str(exc).lower()


def test_delete_custom_contact(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    added = oc.add_custom_contact(tg_nick="someone", resolve=False)
    assert oc.delete_custom_contact(added["id"]) is True
    assert oc.get_contact(added["id"]) is None
    assert oc.delete_custom_contact(added["id"]) is False


def test_list_merges_catalog_clients(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    oc.add_custom_contact(name="Custom", tg_nick="custom_nick", resolve=False)
    catalog = [
        {"id": "cp-1", "name": "Мария", "tg_nick": "maria_flowers"},
        {"id": "cp-2", "name": "NoTG", "tg_nick": ""},
    ]
    listed = oc.list_outreach_contacts(catalog_clients=catalog, q="mar")
    assert len(listed) == 1
    assert listed[0]["id"] == "cp-1"
    assert listed[0]["source"] == "catalog"


def test_list_keeps_custom_first_within_limit(tmp_path, monkeypatch):
    """Custom rows must not be truncated away by a full Telegram peer list."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(oc, "telegram_account_contacts", lambda refresh=False: [])
    custom = oc.add_custom_contact(
        name="Яна Добавленная",
        tg_nick="yana_added",
        tg_chat_id="999",
        resolve=False,
    )
    catalog = [
        {
            "id": f"cp-{i}",
            "name": f"Клиент {i:03d}",
            "tg_nick": f"user{i}",
            "tg_chat_id": str(1000 + i),
        }
        for i in range(50)
    ]
    listed = oc.list_outreach_contacts(catalog_clients=catalog, limit=20)
    assert listed[0]["id"] == custom["id"]
    assert listed[0]["source"] == "custom"
    assert all(c["source"] == "custom" for c in listed if c["id"] == custom["id"])
    assert len(listed) == 20
    assert sum(1 for c in listed if c.get("source") == "custom") == 1


def test_seller_settings_seeds_biz_id_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_BUSINESS_CONNECTION_ID", "I2EgEdMmiEvLHAAAmN_NBVfktgQ")
    monkeypatch.delenv("MOYSKLAD_TELEGRAM_BUSINESS_CONNECTION_ID", raising=False)
    from plugins.moysklad import campaigns

    settings = campaigns.get_seller_settings()
    assert settings["telegram_business_connection_id"] == "I2EgEdMmiEvLHAAAmN_NBVfktgQ"


def test_resolve_peer_identity_via_get_chat(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BUSINESS_BOT_TOKEN", "1:TEST")
    monkeypatch.delenv("MOYSKLAD_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    class _Resp:
        content = (
            b'{"ok":true,"result":{"id":415321451,"username":"papa2139",'
            b'"first_name":"Asya","type":"private"}}'
        )

        def json(self):
            return json.loads(self.content)

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None, params=None):
            assert "getChat" in url
            assert params["chat_id"] == "@papa2139"
            return _Resp()

    monkeypatch.setattr(tb.httpx, "Client", _Client)
    out = tg.resolve_peer_identity(query="@papa2139")
    assert out["ok"] is True
    assert out["tg_chat_id"] == "415321451"
    assert out["tg_nick"] == "papa2139"
    assert out["resolved_via"] == "getChat"
    assert "Asya" in (out.get("name") or "")


def test_resolve_peer_identity_tme_link(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BUSINESS_BOT_TOKEN", "1:TEST")

    class _Resp:
        content = b'{"ok":true,"result":{"id":99,"username":"maria","type":"private"}}'

        def json(self):
            return json.loads(self.content)

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None, params=None):
            assert params["chat_id"] == "@maria"
            return _Resp()

    monkeypatch.setattr(tb.httpx, "Client", _Client)
    out = tg.resolve_peer_identity(query="https://t.me/maria")
    assert out["ok"] is True
    assert out["tg_chat_id"] == "99"
    assert out["tg_nick"] == "maria"


def test_add_custom_contact_resolves_via_api(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_BUSINESS_BOT_TOKEN", "1:TEST")

    class _Resp:
        content = (
            b'{"ok":true,"result":{"id":415321451,"username":"papa2139",'
            b'"first_name":"Asya","type":"private"}}'
        )

        def json(self):
            return json.loads(self.content)

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None, params=None):
            return _Resp()

    monkeypatch.setattr(tb.httpx, "Client", _Client)
    added = oc.add_custom_contact(query="@papa2139", resolve=True)
    assert added["tg_chat_id"] == "415321451"
    assert added["tg_nick"] == "papa2139"
    assert added["resolved_via"] == "getChat"
    assert added["name"] == "Asya"


def test_resolve_numeric_passthrough_when_getchat_fails(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BUSINESS_BOT_TOKEN", "1:TEST")

    class _Resp:
        content = b'{"ok":false,"error_code":400,"description":"Bad Request: chat not found"}'

        def json(self):
            return json.loads(self.content)

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None, params=None):
            return _Resp()

    monkeypatch.setattr(tb.httpx, "Client", _Client)
    out = tg.resolve_peer_identity(tg_chat_id="415321451")
    assert out["ok"] is True
    assert out["tg_chat_id"] == "415321451"
    assert out["resolved_via"] == "numeric"


def _seed_telegram_contacts(monkeypatch, rows):
    monkeypatch.setattr(oc, "telegram_account_contacts", lambda **k: rows)


def test_personal_telegram_contacts_appear_in_picker(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _seed_telegram_contacts(
        monkeypatch,
        [
            {"tg_chat_id": "415321451", "tg_nick": "papa2139", "name": "Ася"},
            {"tg_chat_id": "777", "tg_nick": "", "name": "Иван"},
        ],
    )
    listed = oc.list_outreach_contacts()
    ids = {c["id"] for c in listed}
    assert ids == {"tg:415321451", "tg:777"}
    assert all(c["source"] == "telegram" for c in listed)


def test_personal_contact_deduped_against_catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _seed_telegram_contacts(
        monkeypatch, [{"tg_chat_id": "415321451", "tg_nick": "papa2139", "name": "Ася"}]
    )
    listed = oc.list_outreach_contacts(
        catalog_clients=[
            {"id": "ms-1", "name": "Ася Иванова", "tg_nick": "papa2139"},
        ]
    )
    assert [c["id"] for c in listed] == ["ms-1"]


def test_get_contact_resolves_tg_id(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _seed_telegram_contacts(
        monkeypatch, [{"tg_chat_id": "415321451", "tg_nick": "papa2139", "name": "Ася"}]
    )
    got = oc.get_contact("tg:415321451")
    assert got is not None
    assert got["tg_nick"] == "papa2139"
    assert got["name"] == "Ася"


def test_get_contact_tg_id_survives_cache_miss(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _seed_telegram_contacts(monkeypatch, [])
    got = oc.get_contact("tg:415321451")
    assert got is not None
    assert got["tg_chat_id"] == "415321451"
