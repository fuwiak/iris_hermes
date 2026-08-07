"""Telegram Business platform plugin — Office registration smoke."""

from __future__ import annotations

from gateway.config import PlatformConfig
from plugins.platforms.telegram_business import adapter as tba


def test_validate_config_requires_token_and_connection(monkeypatch):
    for key in (
        "TELEGRAM_BUSINESS_BOT_TOKEN",
        "TELEGRAM_BUSINESS_CONNECTION_ID",
        "MOYSKLAD_TELEGRAM_BOT_TOKEN",
        "MOYSKLAD_TELEGRAM_BUSINESS_CONNECTION_ID",
        "TELEGRAM_BOT_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)

    cfg = PlatformConfig(enabled=True)
    assert tba.validate_config(cfg) is False

    monkeypatch.setenv("TELEGRAM_BUSINESS_BOT_TOKEN", "1:TOK")
    assert tba.validate_config(cfg) is False

    monkeypatch.setenv("TELEGRAM_BUSINESS_CONNECTION_ID", "biz-1")
    assert tba.validate_config(cfg) is True
    assert tba.is_connected(cfg) is True


def test_env_enablement_seeds_extra(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BUSINESS_BOT_TOKEN", "1:TOK")
    monkeypatch.setenv("TELEGRAM_BUSINESS_CONNECTION_ID", "biz-9")
    monkeypatch.setenv("TELEGRAM_BUSINESS_BOT_USERNAME", "BizBot")
    monkeypatch.delenv("MOYSKLAD_TELEGRAM_BUSINESS_CONNECTION_ID", raising=False)
    seed = tba._env_enablement()
    assert seed is not None
    assert seed["business_connection_id"] == "biz-9"
    assert seed["bot_username"] == "BizBot"


def test_probe_missing_token(monkeypatch):
    for key in (
        "TELEGRAM_BUSINESS_BOT_TOKEN",
        "MOYSKLAD_TELEGRAM_BOT_TOKEN",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_BUSINESS_CONNECTION_ID",
        "MOYSKLAD_TELEGRAM_BUSINESS_CONNECTION_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    from plugins.platforms.telegram_business.client import probe_business_integration

    out = probe_business_integration()
    assert out["ok"] is False
    assert "token" in (out.get("message") or "").lower() or out.get("bot", {}).get("error")
