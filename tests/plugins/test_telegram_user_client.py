"""Tests for the personal-account (MTProto) Telegram client.

No Telethon and no network: everything that touches Telegram goes through
``_call`` / ``is_authorized``, which the tests stub.
"""

from __future__ import annotations

import json
import time

import plugins.platforms.telegram_user.client as tu


def _clear_env(monkeypatch, *, builtin: bool = False):
    for key in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_USER_SESSION"):
        monkeypatch.delenv(key, raising=False)
    if builtin:
        monkeypatch.delenv("TELEGRAM_BUILTIN_API", raising=False)
    else:
        monkeypatch.setenv("TELEGRAM_BUILTIN_API", "0")


def test_status_without_credentials(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    status = tu.user_status()
    assert status["ok"] is True
    assert status["api_configured"] is False
    assert status["session_saved"] is False
    assert status["authorized"] is False


def test_builtin_credentials_fallback(tmp_path, monkeypatch):
    """No env, no config → built-in app keys so login needs only the phone."""
    _clear_env(monkeypatch, builtin=True)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    api_id, api_hash = tu.api_credentials()
    assert api_id.isdigit() and api_hash
    assert tu.own_api_credentials() == ("", "")
    status = tu.user_status(probe=False)
    assert status["api_configured"] is True
    assert status["api_source"] == "builtin"
    # Builtin keys are not the operator's — nothing to preview in the form.
    assert status["api_id_masked"] == ""
    assert status["api_hash_masked"] == ""


def test_own_credentials_beat_builtin(tmp_path, monkeypatch):
    _clear_env(monkeypatch, builtin=True)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    tu.save_credentials(api_id="123456", api_hash="deadbeefcafebabe")
    assert tu.api_credentials() == ("123456", "deadbeefcafebabe")
    assert tu.user_status(probe=False)["api_source"] == "config"


def test_save_credentials_rejects_non_numeric_api_id(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    out = tu.save_credentials(api_id="abc", api_hash="deadbeef", strict=True)
    assert out["ok"] is False
    assert out["error"] == "api_id_invalid"


def test_save_credentials_login_ignores_junk_api_id(tmp_path, monkeypatch):
    """Leftover UI junk must not wipe / block env credentials on login path."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_API_ID", "29924508")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abcdef0123456789abcdef0123456789")
    out = tu.save_credentials(api_id="admin", api_hash="••••", strict=False)
    assert out["ok"] is True
    assert tu.api_credentials() == (
        "29924508",
        "abcdef0123456789abcdef0123456789",
    )


def test_sanitize_api_helpers():
    assert tu.sanitize_api_id("29924508") == "29924508"
    assert tu.sanitize_api_id("admin") == ""
    assert tu.sanitize_api_id("29••••") == ""
    assert tu.sanitize_api_hash("deadbeefcafebabe") == "deadbeefcafebabe"
    assert tu.sanitize_api_hash("admin") == ""
    assert tu.sanitize_api_hash("••••••••") == ""


def test_save_credentials_persists_0600(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert tu.save_credentials(api_id="123456", api_hash="deadbeefcafebabe")["ok"] is True
    assert tu.api_credentials() == ("123456", "deadbeefcafebabe")
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


def test_normalize_login_phone():
    assert tu.normalize_login_phone("+7 (995) 099-81-70") == "+79950998170"
    assert tu.normalize_login_phone("+7 968 540 8368") == "+79685408368"
    assert tu.normalize_login_phone("968 540 8368") == "+79685408368"
    assert tu.normalize_login_phone("89950998170") == "+79950998170"
    assert tu.normalize_login_phone("0079950998170") == "+79950998170"
    assert tu.normalize_login_phone("") == ""
    # Multi-number MoySklad cell: probe the FIRST number, not the concatenation.
    assert (
        tu.normalize_login_phone("+7 982 235-21-88, +7 977 575-80-58")
        == "+79822352188"
    )
    assert tu.normalize_login_phone("89822352188; 89775758058") == "+79822352188"


def test_looks_like_phone_not_user_id():
    assert tu.looks_like_phone("+79001234567") is True
    assert tu.looks_like_phone("79001234567") is True
    assert tu.looks_like_phone("89001234567") is True
    assert tu.looks_like_phone("9001234567") is True
    assert tu.looks_like_phone("415321451") is False
    assert tu.phone_lookup_key("+7 (900) 123-45-67") == "9001234567"
    assert tu.looks_like_phone("+7 982 235-21-88, +7 977 575-80-58") is True
    assert tu.phone_lookup_key("+7 982 235-21-88, +7 977 575-80-58") == "9822352188"


def test_telethon_proxy_arg_socks5():
    proxy = tu.telethon_proxy_arg("socks5://user:pass@127.0.0.1:1080")
    assert proxy[0] == "socks5"
    assert proxy[1] == "127.0.0.1"
    assert proxy[2] == 1080
    assert proxy[4] == "user"
    assert proxy[5] == "pass"
    assert tu.telethon_proxy_arg("") is None
    assert tu.telethon_proxy_arg("not-a-url") is None


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


def test_merge_contacts_incremental(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("REDIS_URL", raising=False)
    total = tu._merge_contacts(
        [{"id": "1", "tg_chat_id": "1", "name": "Anna", "peer_source": "contact"}]
    )
    assert total == 1
    total = tu._merge_contacts(
        [
            # A dialog row must not overwrite the saved contact's name.
            {"id": "1", "tg_chat_id": "1", "name": "@anna", "peer_source": "dialog"},
            {"id": "2", "tg_chat_id": "2", "name": "Boris", "peer_source": "dialog"},
        ]
    )
    assert total == 2
    by_id = {c["id"]: c for c in tu.cached_contacts()}
    assert by_id["1"]["name"] == "Anna"
    assert by_id["2"]["peer_source"] == "dialog"


def test_start_contacts_sync_fresh_cache_short_circuits(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("REDIS_URL", raising=False)
    tu._merge_contacts(
        [{"id": "1", "tg_chat_id": "1", "name": "Anna", "peer_source": "contact"}]
    )
    out = tu.start_contacts_sync(force=False)
    assert out["ok"] is True
    assert out["started"] is False
    assert out["cached"] is True
    assert out["total"] == 1
    assert tu.contacts_sync_status()["running"] is False


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
    assert tu.find_cached_contact(phone="+7 999 000 11 22") is not None
    assert tu.find_cached_contact(phone="89990001122") is not None
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


def test_sent_code_meta_app_vs_sms():
    class _AppType:
        pass

    class _SmsType:
        pass

    class _Sent:
        def __init__(self, t):
            self.type = t

    app = tu._sent_code_meta(_Sent(_AppType()))
    assert app["code_delivery"] == "telegram_app"
    assert "не SMS" in app["code_delivery_hint"]

    sms = tu._sent_code_meta(_Sent(_SmsType()))
    assert sms["code_delivery"] == "sms"
    assert "SMS" in sms["code_delivery_hint"]


def test_gateway_start_login_forwards_force_sms(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_USER_GATEWAY_URL", "https://eg.example/t/tok")
    calls: list[dict] = []

    def _fake(method, path, **kwargs):
        calls.append(kwargs.get("json_body") or {})
        return {
            "ok": True,
            "code_sent": True,
            "code_delivery": "sms",
            "via": "gateway",
        }

    monkeypatch.setattr(tu, "_gateway_request", _fake)
    out = tu.start_login(phone="+7 968 540 8368", force_sms=True)
    assert out["ok"] is True
    assert calls == [{"phone": "+79685408368", "api_id": "", "api_hash": "", "force_sms": True}]


def test_gateway_start_login_forwards(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_USER_GATEWAY_URL", "https://eg.example/t/tok")
    calls: list[tuple] = []

    def _fake(method, path, **kwargs):
        calls.append((method, path, kwargs.get("json_body")))
        return {"ok": True, "code_sent": True, "via": "gateway"}

    monkeypatch.setattr(tu, "_gateway_request", _fake)
    out = tu.start_login(phone="+7 968 540 8368")
    assert out["ok"] is True
    assert out["code_sent"] is True
    assert out["phone"] == "+79685408368"
    assert calls == [
        ("POST", "login", {"phone": "+79685408368", "api_id": "", "api_hash": "", "force_sms": False})
    ]
    assert tu.load_config().get("phone") == "+79685408368"


def test_gateway_submit_code_skips_local_persist(tmp_path, monkeypatch):
    """Gateway login must not hang on Selectel trying local MTProto after code."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_USER_GATEWAY_URL", "https://eg.example/t/tok")

    def _fake(method, path, **kwargs):
        assert method == "POST" and path == "code"
        assert kwargs["json_body"] == {"code": "12345"}
        return {
            "ok": True,
            "authorized": True,
            "user": {"id": 1, "username": "iris", "phone": "+79685408368"},
        }

    monkeypatch.setattr(tu, "_gateway_request", _fake)

    def _boom(*_a, **_k):
        raise AssertionError("local Telethon must not run when gateway is set")

    monkeypatch.setattr(tu, "_call", _boom)
    monkeypatch.setattr(tu, "start_contacts_sync", lambda **_: {"ok": True, "started": False})

    out = tu.submit_code(" 12345 ")
    assert out["authorized"] is True
    cfg = tu.load_config()
    assert cfg["user"]["username"] == "iris"
    assert cfg["phone"] == "+79685408368"


def test_gateway_ensure_runtime_skips_telethon(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_USER_GATEWAY_URL", "https://eg.example/t/tok")
    out = tu.ensure_runtime()
    assert out == {"ok": True, "available": True, "via": "gateway"}


def test_gateway_send_message_forwards(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_USER_GATEWAY_URL", "https://eg.example/t/tok")

    def _fake(method, path, **kwargs):
        assert method == "POST" and path == "send"
        assert kwargs["json_body"] == {"peer": "@x", "text": "hi"}
        return {"ok": True, "message_id": 1, "via": "gateway"}

    monkeypatch.setattr(tu, "_gateway_request", _fake)
    out = tu.send_message(peer="@x", text="hi")
    assert out["ok"] is True
    assert out["via"] == "gateway"


def test_gateway_resolve_peer_forwards(tmp_path, monkeypatch):
    """Selectel must not open local MTProto when resolving @nick via egress."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_USER_GATEWAY_URL", "https://eg.example/t/tok")
    calls: list[tuple] = []

    def _fake(method, path, **kwargs):
        calls.append((method, path, kwargs.get("json_body")))
        return {
            "ok": True,
            "tg_chat_id": "415321451",
            "tg_nick": "AnnaV_dess",
            "name": "Anna",
            "resolved_via": "mtproto_gateway",
            "via": "gateway",
        }

    monkeypatch.setattr(tu, "_gateway_request", _fake)

    def _boom(*a, **k):
        raise AssertionError("local Telethon must not run when gateway is set")

    monkeypatch.setattr(tu, "_call", _boom)
    out = tu.resolve_peer("@AnnaV_dess")
    assert out["ok"] is True
    assert out["tg_chat_id"] == "415321451"
    assert out["tg_nick"] == "AnnaV_dess"
    assert calls == [("POST", "resolve", {"peer": "@AnnaV_dess"})]


def test_resolve_peer_uses_contacts_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("TELEGRAM_USER_GATEWAY_URL", raising=False)
    monkeypatch.setattr(
        tu,
        "cached_contacts",
        lambda: [
            {
                "id": "99",
                "tg_chat_id": "99",
                "tg_nick": "cacheduser",
                "name": "Cached",
            }
        ],
    )

    def _boom(*a, **k):
        raise AssertionError("cache hit must skip Telethon/gateway")

    monkeypatch.setattr(tu, "_call", _boom)
    monkeypatch.setattr(tu, "_gateway_request", _boom)
    out = tu.resolve_peer("@cacheduser")
    assert out["ok"] is True
    assert out["tg_chat_id"] == "99"
    assert out["resolved_via"] == "contacts_cache"


def test_resolve_peer_phone_uses_contacts_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("TELEGRAM_USER_GATEWAY_URL", raising=False)
    monkeypatch.setattr(
        tu,
        "cached_contacts",
        lambda: [
            {
                "id": "77",
                "tg_chat_id": "77",
                "tg_nick": "flower",
                "name": "Клиент",
                "phone": "79001112233",
            }
        ],
    )

    def _boom(*a, **k):
        raise AssertionError("phone cache hit must skip Telethon/gateway")

    monkeypatch.setattr(tu, "_call", _boom)
    monkeypatch.setattr(tu, "_gateway_request", _boom)
    out = tu.resolve_peer("+7 900 111-22-33")
    assert out["ok"] is True
    assert out["tg_chat_id"] == "77"
    assert out["resolved_via"] == "contacts_cache"


def test_gateway_start_contacts_sync_uses_egress(tmp_path, monkeypatch):
    """Selectel contact sync must not open local MTProto when gateway URL is set."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_USER_GATEWAY_URL", "https://eg.example/t/tok")
    monkeypatch.delenv("REDIS_URL", raising=False)
    with tu._LOCK:
        tu._SYNC_STATE.update(
            running=False,
            phase="",
            scanned=0,
            total=0,
            from_address_book=0,
            from_dialogs=0,
            started_at=0.0,
            finished_at=0.0,
            error="",
        )
    calls: list[tuple] = []

    def _fake(method, path, **kwargs):
        calls.append((method, path))
        return {
            "ok": True,
            "contacts": [
                {
                    "id": "9",
                    "tg_chat_id": "9",
                    "tg_nick": "@alice",
                    "name": "Alice",
                    "source": "contact",
                }
            ],
            "from_address_book": 1,
            "from_dialogs": 0,
            "via": "gateway",
        }

    monkeypatch.setattr(tu, "_gateway_request", _fake)
    out = tu.start_contacts_sync(force=True)
    assert out["started"] is True
    deadline = time.time() + 2.0
    while time.time() < deadline:
        st = tu.contacts_sync_status()
        if not st.get("running") and st.get("phase") in {"done", "error"}:
            break
        time.sleep(0.02)
    st = tu.contacts_sync_status()
    assert st["phase"] == "done"
    assert st["error"] == ""
    assert st["total"] == 1
    assert calls == [("POST", "contacts/refresh")]
    cached = tu.cached_contacts()
    assert cached[0]["tg_nick"] == "alice"
    assert cached[0]["peer_source"] == "contact"


def test_normalize_gateway_contact_strips_at():
    row = tu._normalize_gateway_contact(
        {"id": "1", "tg_chat_id": "1", "tg_nick": "@bob", "name": "Bob", "source": "dialog"}
    )
    assert row == {
        "id": "1",
        "tg_chat_id": "1",
        "tg_nick": "bob",
        "name": "Bob",
        "phone": "",
        "peer_source": "dialog",
    }


def test_gateway_cheap_poll_keeps_cached_identity(tmp_path, monkeypatch):
    """probe=false never carries authorized=True from the egress — the status
    must fall back to the last real probe instead of flashing «не авторизована»."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_USER_GATEWAY_URL", "https://eg.example/t/tok")

    def _fake(method, path, **kwargs):
        probing = kwargs["params"]["probe"] == "true"
        return {
            "ok": True,
            "session_saved": True,
            "authorized": probing,
            "phone": "+79991234567",
            "user": {"id": 1, "username": "pstasinski"} if probing else None,
        }

    monkeypatch.setattr(tu, "_gateway_request", _fake)
    real = tu.user_status(probe=True)
    assert real["authorized"] is True

    cheap = tu.user_status(probe=False)
    assert cheap["authorized"] is True
    assert cheap["authorized_cached"] is True
    assert cheap["user"]["username"] == "pstasinski"


def test_gateway_real_probe_logout_clears_cached_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_USER_GATEWAY_URL", "https://eg.example/t/tok")
    responses = [
        {"ok": True, "session_saved": True, "authorized": True,
         "user": {"id": 1, "username": "pstasinski"}},
        {"ok": True, "session_saved": True, "authorized": False, "user": None},
        {"ok": True, "session_saved": True, "authorized": False, "user": None},
    ]

    monkeypatch.setattr(tu, "_gateway_request", lambda *a, **k: responses.pop(0))
    assert tu.user_status(probe=True)["authorized"] is True
    # Session revoked: the real probe reports it and must drop the cache…
    assert tu.user_status(probe=True)["authorized"] is False
    # …so the next cheap poll agrees instead of serving the stale ✓.
    assert tu.user_status(probe=False)["authorized"] is False


def test_local_cheap_poll_uses_cached_user(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    tu._save_config({"session": "s" * 40, "user": {"id": 1, "username": "px"}})
    st = tu.user_status(probe=False)
    assert st["session_saved"] is True
    assert st["authorized"] is True
    assert st["authorized_cached"] is True


def test_fetch_history_via_gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_USER_GATEWAY_URL", "https://eg.example/t/tok")
    captured: dict = {}

    def _req(method, path, **kwargs):
        captured["method"] = method
        captured["path"] = path
        captured["body"] = kwargs.get("json_body")
        return {
            "ok": True,
            "messages": [
                {"direction": "inbound", "text": "hi", "ts": "2026-08-01T00:00:00Z"}
            ],
            "tg_chat_id": "99",
            "count": 1,
        }

    monkeypatch.setattr(tu, "_gateway_request", _req)
    out = tu.fetch_history(peer="@buyer", limit=20)
    assert out["ok"] is True
    assert captured["path"] == "history"
    assert captured["body"]["peer"] == "@buyer"
    assert out["messages"][0]["direction"] == "inbound"


def test_consume_import_contacts_matches_users_by_phone_when_imported_empty():
    """Already-saved contacts come back in ``users`` with a phone, not ``imported``.

    Treating that as «нет TG» was the bulk false-negative the operator hit
    when the same number still resolved in the Telegram search box.
    """
    from types import SimpleNamespace

    user = SimpleNamespace(
        id=111,
        username="ann",
        first_name="Ann",
        last_name="",
        phone="79001112233",
        bot=False,
    )
    result = SimpleNamespace(imported=[], retry_contacts=[], users=[user])
    parsed = tu.consume_import_contacts(result, ["+79001112233", "+79009998877"])
    assert "+79001112233" in parsed["found"]
    assert parsed["found"]["+79001112233"]["tg_chat_id"] == "111"
    assert parsed["found"]["+79001112233"]["tg_nick"] == "ann"
    assert "+79009998877" not in parsed["found"]
    assert parsed["retried"] == set()


def test_consume_single_phone_hidden_phone_field_still_counts():
    """iOS New Contact: importContacts returns User with empty phone + empty imported."""
    from types import SimpleNamespace

    user = SimpleNamespace(
        id=9856254519,
        username="",
        first_name="A",
        last_name="",
        phone="",
        bot=False,
        is_self=False,
    )
    result = SimpleNamespace(imported=[], retry_contacts=[], users=[user])
    parsed = tu.consume_import_contacts(result, ["+79856254519"])
    assert parsed["found"]["+79856254519"]["tg_chat_id"] == "9856254519"


def test_phone_import_strings_digits_first():
    variants = tu._phone_import_strings("+7 985 625-45-19")
    assert variants[0] == "79856254519"
    assert "+79856254519" in variants


def test_consume_import_contacts_maps_1based_imported_and_retry():
    from types import SimpleNamespace

    user = SimpleNamespace(
        id=222,
        username="bob",
        first_name="Bob",
        last_name="",
        phone="",
        bot=False,
    )
    imported = SimpleNamespace(client_id=1, user_id=222)
    result = SimpleNamespace(
        imported=[imported],
        retry_contacts=[2],
        users=[user],
    )
    phones = ["+79001110001", "+79001110002"]
    parsed = tu.consume_import_contacts(result, phones)
    assert parsed["found"]["+79001110001"]["tg_chat_id"] == "222"
    assert parsed["retried"] == {"+79001110002"}
    assert parsed["imported_users"] == [user]


def test_is_phone_unoccupied_error():
    class PhoneNumberUnoccupiedError(Exception):
        pass

    assert tu._is_phone_unoccupied_error(PhoneNumberUnoccupiedError("x")) is True
    assert tu._is_phone_unoccupied_error(RuntimeError("PHONE_NOT_OCCUPIED")) is True
    assert tu._is_phone_unoccupied_error(RuntimeError("network down")) is False
