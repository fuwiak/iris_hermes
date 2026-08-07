"""Tests for MoySklad Telegram Business outreach send."""

from __future__ import annotations

import json

import plugins.moysklad.telegram_send as tg
import plugins.platforms.telegram_business.client as tb


def _clear_biz_env(monkeypatch):
    for key in (
        "TELEGRAM_BUSINESS_BOT_TOKEN",
        "TELEGRAM_BUSINESS_CONNECTION_ID",
        "TELEGRAM_BUSINESS_BOT_USERNAME",
        "MOYSKLAD_TELEGRAM_BOT_TOKEN",
        "MOYSKLAD_TELEGRAM_BUSINESS_CONNECTION_ID",
        "MOYSKLAD_TELEGRAM_BOT_USERNAME",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_BOT_USERNAME",
    ):
        monkeypatch.delenv(key, raising=False)
    # These cases exercise the Business bot path — pin it so a connected
    # personal account on the dev machine can't take over the send.
    monkeypatch.setenv("MOYSKLAD_TELEGRAM_SEND_VIA", "bot")


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
    _clear_biz_env(monkeypatch)
    out = tg.send_telegram_message(text="hi", chat_id="@x")
    assert out["ok"] is False
    assert out["error"] == "telegram_token_missing"


def test_business_env_precedes_moysklad_alias(monkeypatch):
    _clear_biz_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BUSINESS_BOT_TOKEN", "1:BIZ")
    monkeypatch.setenv("MOYSKLAD_TELEGRAM_BOT_TOKEN", "1:LEGACY")
    assert tg.outreach_bot_token() == "1:BIZ"
    monkeypatch.setenv("TELEGRAM_BUSINESS_CONNECTION_ID", "biz-new")
    monkeypatch.setenv("MOYSKLAD_TELEGRAM_BUSINESS_CONNECTION_ID", "biz-old")
    assert tg.resolve_business_connection_id() == "biz-new"


def test_send_posts_business_connection(monkeypatch):
    _clear_biz_env(monkeypatch)
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

    monkeypatch.setattr(tb.httpx, "Client", _Client)
    out = tg.send_telegram_message(text="Здравствуйте!", chat_id="@client")
    assert out["ok"] is True
    assert out["message_id"] == 7
    assert "1:TESTTOKEN" in captured["url"]
    assert captured["json"]["chat_id"] == 42
    assert captured["json"]["business_connection_id"] == "biz-abc"
    assert captured["json"]["text"] == "Здравствуйте!"


def test_outreach_uses_client_nick(monkeypatch):
    _clear_biz_env(monkeypatch)
    monkeypatch.setenv("MOYSKLAD_TELEGRAM_BOT_TOKEN", "1:TEST")

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

    monkeypatch.setattr(tb.httpx, "Client", _Client)
    out = tg.send_outreach_to_client(text="ping", tg_nick="nick")
    assert out["ok"] is True


def test_fetch_business_connection_parses_rights(monkeypatch):
    _clear_biz_env(monkeypatch)
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

    monkeypatch.setattr(tb.httpx, "Client", _Client)
    out = tg.fetch_business_connection()
    assert out["ok"] is True
    assert out["can_reply"] is True
    assert out["can_read_messages"] is True
    assert out["user_username"] == "owner"


def test_coerce_business_chat_id_numeric_passthrough():
    out = tg.coerce_business_chat_id("123456789")
    assert out == {"ok": True, "chat_id": "123456789", "resolved_via": "numeric"}


def test_coerce_business_chat_id_via_get_chat(monkeypatch):
    _clear_biz_env(monkeypatch)
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

    monkeypatch.setattr(tb.httpx, "Client", _Client)
    out = tg.coerce_business_chat_id("https://t.me/papa2139")
    assert out["ok"] is True
    assert out["chat_id"] == "4242"
    assert out["resolved_via"] == "getChat"


def test_coerce_business_chat_id_unresolved(monkeypatch):
    _clear_biz_env(monkeypatch)
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

    monkeypatch.setattr(tb.httpx, "Client", _Client)
    out = tg.coerce_business_chat_id("@papa2139")
    assert out["ok"] is False
    assert out["error"] == "telegram_chat_unresolved"


def test_telegram_account_snapshot_probes_connection(monkeypatch):
    _clear_biz_env(monkeypatch)
    monkeypatch.setenv("MOYSKLAD_TELEGRAM_BOT_TOKEN", "1:TEST")
    monkeypatch.setenv("MOYSKLAD_TELEGRAM_BOT_USERNAME", "BoberSystemsAssistant_bot")
    monkeypatch.setenv("MOYSKLAD_TELEGRAM_BUSINESS_CONNECTION_ID", "biz-xyz")

    monkeypatch.setattr(
        tb,
        "fetch_business_connection",
        lambda *a, **k: {
            "ok": True,
            "id": "biz-xyz",
            "is_enabled": True,
            "can_reply": True,
            "can_read_messages": False,
            "user_username": "pstasinski",
            "user_first_name": "Паша",
            "user_chat_id": 5305427956,
            "rights": {"can_reply": True},
        },
    )
    snap = tg.telegram_account_snapshot()
    assert snap["configured"] is True
    assert snap["bot_username"] == "BoberSystemsAssistant_bot"
    assert snap["business_connection_id"] == "biz-xyz"
    assert snap["account"]["ok"] is True
    assert snap["account"]["username"] == "pstasinski"
    assert snap["account"]["can_reply"] is True


def test_telegram_send_status_includes_connection_id(monkeypatch):
    _clear_biz_env(monkeypatch)
    monkeypatch.setenv("MOYSKLAD_TELEGRAM_BOT_TOKEN", "1:TEST")
    monkeypatch.setenv("MOYSKLAD_TELEGRAM_BUSINESS_CONNECTION_ID", "biz-1")
    status = tg.telegram_send_status()
    assert status["business_connection_configured"] is True
    assert status["business_connection_id"] == "biz-1"


def test_office_platform_override_lists_telegram_business():
    from hermes_cli.web_server import _PLATFORM_OVERRIDES, _PLATFORM_ORDER

    assert "telegram_business" in _PLATFORM_OVERRIDES
    assert _PLATFORM_OVERRIDES["telegram_business"]["name"] == "Telegram Business"
    assert "telegram_business" in _PLATFORM_ORDER
    assert _PLATFORM_ORDER.index("telegram_business") == _PLATFORM_ORDER.index("telegram") + 1


# ── personal account (MTProto) routing ────────────────────────────────────


def _fake_user_account(monkeypatch, *, authorized=True, result=None):
    monkeypatch.setattr(tg.tg_user, "is_authorized", lambda **k: authorized)
    monkeypatch.setattr(
        tg.tg_user,
        "send_message",
        lambda **kwargs: result
        or {"ok": True, "message_id": 11, "chat_id": "415321451", "via": "user_account"},
    )
    monkeypatch.setattr(tg.tg_user, "load_config", lambda: {"user": {"username": "pawel"}})


def test_auto_prefers_personal_account(monkeypatch):
    _clear_biz_env(monkeypatch)
    monkeypatch.delenv("MOYSKLAD_TELEGRAM_SEND_VIA", raising=False)
    _fake_user_account(monkeypatch)
    sent: list[dict] = []
    monkeypatch.setattr(tg, "telegram_api", lambda *a, **k: sent.append(k) or {"ok": True})

    out = tg.send_telegram_message(text="привет", chat_id="@papa2139")
    assert out["ok"] is True
    assert out["via"] == "user_account"
    assert out["user_username"] == "pawel"
    assert sent == []  # Bot API untouched


def test_auto_falls_back_to_business_bot(monkeypatch):
    _clear_biz_env(monkeypatch)
    monkeypatch.delenv("MOYSKLAD_TELEGRAM_SEND_VIA", raising=False)
    monkeypatch.setenv("TELEGRAM_BUSINESS_BOT_TOKEN", "1:BIZ")
    _fake_user_account(
        monkeypatch,
        result={"ok": False, "error": "FloodWaitError", "detail": "wait"},
    )
    monkeypatch.setattr(
        tg,
        "telegram_api",
        lambda *a, **k: {"ok": True, "result": {"message_id": 5, "chat": {"id": 42}}},
    )
    out = tg.send_telegram_message(text="привет", chat_id="415321451")
    assert out["ok"] is True
    assert out["via"] == "business_bot"


def test_via_user_does_not_fall_back(monkeypatch):
    _clear_biz_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BUSINESS_BOT_TOKEN", "1:BIZ")
    _fake_user_account(monkeypatch, authorized=False)
    out = tg.send_telegram_message(text="hi", chat_id="@x", via="user")
    assert out["ok"] is False
    assert out["error"] == "telegram_user_unavailable"


def test_bot_mode_skips_personal_account(monkeypatch):
    _clear_biz_env(monkeypatch)  # pins MOYSKLAD_TELEGRAM_SEND_VIA=bot
    calls: list[str] = []
    monkeypatch.setattr(
        tg.tg_user, "is_authorized", lambda **k: calls.append("probe") or True
    )
    monkeypatch.setattr(tg, "telegram_api", lambda *a, **k: {"ok": False, "error": "x"})
    tg.send_telegram_message(text="hi", chat_id="@x")
    assert calls == []


def test_send_mode_env(monkeypatch):
    monkeypatch.setenv("MOYSKLAD_TELEGRAM_SEND_VIA", "user")
    assert tg.telegram_send_mode() == "user"
    monkeypatch.setenv("MOYSKLAD_TELEGRAM_SEND_VIA", "nonsense")
    assert tg.telegram_send_mode() == "auto"
    monkeypatch.delenv("MOYSKLAD_TELEGRAM_SEND_VIA", raising=False)
    assert tg.telegram_send_mode() == "auto"


def test_resolve_peer_identity_uses_cached_telegram_contact(tmp_path, monkeypatch):
    _clear_biz_env(monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(
        tg.tg_user,
        "find_cached_contact",
        lambda **k: {"tg_nick": "papa2139", "tg_chat_id": "415321451", "name": "Ася"},
    )
    monkeypatch.setattr(tg.tg_user, "is_authorized", lambda **k: False)
    monkeypatch.setattr(tg, "fetch_chat", lambda *a, **k: {"ok": False, "detail": "no"})
    out = tg.resolve_peer_identity(query="@papa2139")
    assert out["ok"] is True
    assert out["tg_chat_id"] == "415321451"
