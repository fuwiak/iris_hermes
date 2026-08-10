"""Business-bot рассылка: flood waits, throttling, per-recipient preflight."""

from __future__ import annotations

from typing import Any

import plugins.moysklad.telegram_send as tg


def _pin_bot(monkeypatch):
    for key in (
        "TELEGRAM_BUSINESS_BOT_TOKEN",
        "TELEGRAM_BUSINESS_CONNECTION_ID",
        "MOYSKLAD_TELEGRAM_BOT_TOKEN",
        "MOYSKLAD_TELEGRAM_BUSINESS_CONNECTION_ID",
        "TELEGRAM_BOT_TOKEN",
        "MOYSKLAD_TELEGRAM_SEND_DELAY_MS",
        "MOYSKLAD_TELEGRAM_FLOOD_WAIT_MAX",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MOYSKLAD_TELEGRAM_SEND_VIA", "bot")
    monkeypatch.setenv("TELEGRAM_BUSINESS_BOT_TOKEN", "1:BIZ")
    monkeypatch.setenv("TELEGRAM_BUSINESS_CONNECTION_ID", "biz-1")


def _flood(retry_after: int) -> dict[str, Any]:
    return {
        "ok": False,
        "error": "telegram_api",
        "error_code": 429,
        "detail": "Too Many Requests",
        "raw": {"parameters": {"retry_after": retry_after}},
    }


def test_flood_wait_is_retried_not_dropped(monkeypatch):
    _pin_bot(monkeypatch)
    calls: list[dict[str, Any]] = []
    slept: list[float] = []

    def fake_api(method, *, token=None, json_body=None, params=None, timeout=30.0):
        calls.append(json_body or {})
        if len(calls) == 1:
            return _flood(2)
        return {"ok": True, "result": {"message_id": 7, "chat_id": {"id": 42}}}

    monkeypatch.setattr(tg, "telegram_api", fake_api)
    monkeypatch.setattr(tg.time, "sleep", lambda s: slept.append(s))

    out = tg.send_telegram_message(text="привет", chat_id="42")
    assert out["ok"] is True
    assert len(calls) == 2
    assert slept == [2.0]


def test_flood_wait_longer_than_ceiling_gives_up(monkeypatch):
    _pin_bot(monkeypatch)
    monkeypatch.setenv("MOYSKLAD_TELEGRAM_FLOOD_WAIT_MAX", "5")
    slept: list[float] = []

    monkeypatch.setattr(
        tg, "telegram_api", lambda *a, **k: _flood(3600)
    )
    monkeypatch.setattr(tg.time, "sleep", lambda s: slept.append(s))

    out = tg.send_telegram_message(text="привет", chat_id="42")
    assert out["ok"] is False
    assert out["retry_after"] == 3600.0
    assert slept == []  # never sit through an hour-long wait


def test_send_delay_is_configurable(monkeypatch):
    monkeypatch.delenv("MOYSKLAD_TELEGRAM_SEND_DELAY_MS", raising=False)
    assert tg.send_delay_seconds() == 0.35
    monkeypatch.setenv("MOYSKLAD_TELEGRAM_SEND_DELAY_MS", "1200")
    assert tg.send_delay_seconds() == 1.2
    monkeypatch.setenv("MOYSKLAD_TELEGRAM_SEND_DELAY_MS", "мусор")
    assert tg.send_delay_seconds() == 0.35


def test_preflight_accepts_numeric_and_rejects_cold_nick(monkeypatch):
    _pin_bot(monkeypatch)
    # Bot API cannot resolve a username it has never seen.
    monkeypatch.setattr(
        tg,
        "telegram_api",
        lambda *a, **k: {"ok": False, "error": "telegram_api", "detail": "chat not found"},
    )
    monkeypatch.setattr(tg, "_lookup_peer_in_local_stores", lambda **k: None)

    assert tg.preflight_recipient(tg_chat_id="123456789")["ok"] is True

    cold = tg.preflight_recipient(tg_nick="@never_wrote_us")
    assert cold["ok"] is False
    assert cold["error"] == "telegram_chat_unresolved"

    missing = tg.preflight_recipient()
    assert missing["error"] == "telegram_chat_missing"


def test_preflight_uses_archive_chat_id(monkeypatch):
    """A peer from the Telegram export is reachable even without getChat."""
    _pin_bot(monkeypatch)
    monkeypatch.setattr(
        tg,
        "telegram_api",
        lambda *a, **k: {"ok": False, "error": "telegram_api", "detail": "chat not found"},
    )
    monkeypatch.setattr(
        tg,
        "_lookup_peer_in_local_stores",
        lambda **k: {"tg_nick": "maria", "tg_chat_id": "999001", "resolved_via": "tg_archive"},
    )

    out = tg.preflight_recipient(tg_nick="@maria")
    assert out["ok"] is True
    assert out["chat_id"] == "999001"
    assert out["resolved_via"] == "tg_archive"


def test_business_preflight_flags_missing_reply_right(monkeypatch):
    _pin_bot(monkeypatch)
    monkeypatch.setattr(
        tg,
        "telegram_account_snapshot",
        lambda **k: {"account": {"ok": True, "can_reply": False, "username": "studio"}},
    )
    out = tg.business_preflight()
    assert out["ok"] is False
    assert out["error"] == "business_cannot_reply"


def test_business_preflight_ok(monkeypatch):
    _pin_bot(monkeypatch)
    monkeypatch.setattr(
        tg,
        "telegram_account_snapshot",
        lambda **k: {"account": {"ok": True, "can_reply": True, "username": "studio"}},
    )
    assert tg.business_preflight()["ok"] is True
