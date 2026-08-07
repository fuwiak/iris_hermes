"""Tests for MoySklad Telegram Business outreach send."""

from __future__ import annotations

import json

import plugins.moysklad.telegram_send as tg


def test_resolve_chat_id_from_nick():
    assert tg.resolve_telegram_chat_id(tg_nick="@maria_flowers") == "@maria_flowers"
    assert tg.resolve_telegram_chat_id(tg_nick="maria") == "@maria"


def test_resolve_chat_id_from_numeric_and_tme(monkeypatch):
    assert tg.resolve_telegram_chat_id(tg_chat_id="123456789") == "123456789"
    assert (
        tg.resolve_telegram_chat_id(tg_conversation="https://t.me/some_user")
        == "@some_user"
    )
    assert (
        tg.resolve_telegram_chat_id(tg_conversation="tg://user?id=987654321")
        == "987654321"
    )


def test_send_missing_token(monkeypatch):
    monkeypatch.delenv("MOYSKLAD_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    out = tg.send_telegram_message(text="hi", chat_id="@x")
    assert out["ok"] is False
    assert out["error"] == "telegram_token_missing"


def test_send_posts_business_connection(monkeypatch):
    monkeypatch.setenv("MOYSKLAD_TELEGRAM_BOT_TOKEN", "1:TESTTOKEN")
    monkeypatch.setenv("MOYSKLAD_TELEGRAM_BUSINESS_CONNECTION_ID", "biz-abc")
    monkeypatch.setenv("MOYSKLAD_TELEGRAM_BOT_USERNAME", "BoberSystemsAssistant_bot")

    captured: dict = {}

    class _Resp:
        def __init__(self, body: bytes):
            self.content = body

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
            if "getChat" in url:
                return _Resp(
                    b'{"ok":true,"result":{"id":42,"username":"client","type":"private"}}'
                )
            captured["url"] = url
            captured["json"] = json
            return _Resp(b'{"ok":true,"result":{"message_id":7,"chat":{"id":42}}}')

    monkeypatch.setattr(tg.httpx, "Client", _Client)
    out = tg.send_telegram_message(text="Здравствуйте!", chat_id="@client")
    assert out["ok"] is True
    assert out["message_id"] == 7
    assert "1:TESTTOKEN" in captured["url"]
    assert captured["json"]["chat_id"] == 42
    assert captured["json"]["business_connection_id"] == "biz-abc"
    assert captured["json"]["text"] == "Здравствуйте!"


def test_outreach_uses_client_nick(monkeypatch):
    monkeypatch.setenv("MOYSKLAD_TELEGRAM_BOT_TOKEN", "1:TEST")
    monkeypatch.delenv("MOYSKLAD_TELEGRAM_BUSINESS_CONNECTION_ID", raising=False)

    class _Resp:
        content = b'{"ok":true,"result":{"message_id":1,"chat":{"id":1}}}'

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
            assert json["chat_id"] == "@nick"
            assert "business_connection_id" not in json
            return _Resp()

    monkeypatch.setattr(tg.httpx, "Client", _Client)
    out = tg.send_outreach_to_client(text="ping", tg_nick="nick")
    assert out["ok"] is True


def test_fetch_business_connection_parses_rights(monkeypatch):
    monkeypatch.setenv("MOYSKLAD_TELEGRAM_BOT_TOKEN", "1:TEST")
    monkeypatch.setenv("MOYSKLAD_TELEGRAM_BUSINESS_CONNECTION_ID", "biz-1")

    class _Resp:
        content = (
            b'{"ok":true,"result":{"id":"biz-1","is_enabled":true,'
            b'"can_reply":true,"rights":{"can_reply":true,"can_read_messages":true},'
            b'"user":{"username":"owner","first_name":"O"},"user_chat_id":9}}'
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
            assert "getBusinessConnection" in url
            assert params["business_connection_id"] == "biz-1"
            return _Resp()

    monkeypatch.setattr(tg.httpx, "Client", _Client)
    out = tg.fetch_business_connection()
    assert out["ok"] is True
    assert out["can_reply"] is True
    assert out["can_read_messages"] is True
    assert out["user_username"] == "owner"


def test_coerce_business_chat_id_numeric_passthrough():
    out = tg.coerce_business_chat_id("123456789")
    assert out == {"ok": True, "chat_id": "123456789", "resolved_via": "numeric"}


def test_coerce_business_chat_id_via_get_chat(monkeypatch):
    monkeypatch.setenv("MOYSKLAD_TELEGRAM_BOT_TOKEN", "1:TEST")

    class _Resp:
        content = b'{"ok":true,"result":{"id":4242,"username":"papa2139","type":"private"}}'

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

    monkeypatch.setattr(tg.httpx, "Client", _Client)
    out = tg.coerce_business_chat_id("https://t.me/papa2139")
    assert out["ok"] is True
    assert out["chat_id"] == "4242"
    assert out["resolved_via"] == "getChat"


def test_coerce_business_chat_id_unresolved(monkeypatch):
    monkeypatch.setenv("MOYSKLAD_TELEGRAM_BOT_TOKEN", "1:TEST")

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

    monkeypatch.setattr(tg.httpx, "Client", _Client)
    out = tg.coerce_business_chat_id("@papa2139")
    assert out["ok"] is False
    assert out["error"] == "telegram_chat_unresolved"
