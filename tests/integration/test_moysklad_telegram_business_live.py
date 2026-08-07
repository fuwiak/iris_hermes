"""Live Telegram Business Bot API smoke for MoySklad outreach.

Uses official Bot API (getMe / getBusinessConnection / getChat / sendMessage)
against @BoberSystemsAssistant_bot. Does NOT call getUpdates (webhook on
Railway owns updates).

``tests/conftest.py`` blanks ``*_TOKEN`` env vars — this module reloads
credentials from ``.env`` / ``~/.hermes/.env`` when LIVE=1.

Run:

    MOYSKLAD_TELEGRAM_LIVE=1 venv/bin/python -m pytest -m integration \\
      tests/integration/test_moysklad_telegram_business_live.py -v -s

Optional:
  MOYSKLAD_TELEGRAM_TEST_CHAT_ID=<numeric>  — integer peer id for @papa2139
  MOYSKLAD_TELEGRAM_TEST_USERNAME=papa2139
  MOYSKLAD_TELEGRAM_TEST_TEXT=...

Business send requires:
  1) connection rights.can_reply (Telegram → Business → Chatbots)
  2) integer chat_id from a prior business_message (cold @username does not resolve)
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import plugins.moysklad.telegram_send as tg

pytestmark = pytest.mark.integration

_LIVE = os.getenv("MOYSKLAD_TELEGRAM_LIVE", "").strip().lower() in {"1", "true", "yes"}

_TOKEN_KEYS = (
    "MOYSKLAD_TELEGRAM_BOT_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "MOYSKLAD_TELEGRAM_BOT_USERNAME",
    "TELEGRAM_BOT_USERNAME",
    "MOYSKLAD_TELEGRAM_BUSINESS_CONNECTION_ID",
    "MOYSKLAD_TELEGRAM_TEST_CHAT_ID",
    "MOYSKLAD_TELEGRAM_TEST_USERNAME",
    "MOYSKLAD_TELEGRAM_TEST_TEXT",
)


def _parse_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:]
        key, val = line.split("=", 1)
        key = key.strip()
        if key not in _TOKEN_KEYS:
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        if " #" in val:
            val = val.split(" #", 1)[0].rstrip()
        out[key] = val
    return out


def _reload_live_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restore Telegram Business creds after conftest credential scrub."""
    merged: dict[str, str] = {}
    for path in (Path.cwd() / ".env", Path.home() / ".hermes" / ".env"):
        merged.update(_parse_env_file(path))
    for key in _TOKEN_KEYS:
        val = merged.get(key) or ""
        if not val and key.startswith("MOYSKLAD_TELEGRAM_TEST_"):
            val = os.environ.get(key, "")
        if val:
            monkeypatch.setenv(key, val)


@pytest.fixture
def live_ready(monkeypatch):
    if not _LIVE:
        pytest.skip("Set MOYSKLAD_TELEGRAM_LIVE=1 to hit live Telegram Business API")
    _reload_live_credentials(monkeypatch)
    if not tg.outreach_bot_token():
        pytest.skip("MOYSKLAD_TELEGRAM_BOT_TOKEN / TELEGRAM_BOT_TOKEN missing in .env")
    if not tg.resolve_business_connection_id():
        pytest.skip("MOYSKLAD_TELEGRAM_BUSINESS_CONNECTION_ID missing in .env")


def test_live_fetch_bot_and_business_connection(live_ready):
    """Ściąga tożsamość bota + BusinessConnection (rights / can_reply)."""
    me = tg.fetch_bot_identity()
    assert me.get("ok") is True, me
    assert me.get("username") == "BoberSystemsAssistant_bot", me

    conn = tg.fetch_business_connection()
    assert conn.get("ok") is True, conn
    assert conn.get("is_enabled") is True, conn
    assert conn.get("id"), conn

    # Surface rights clearly — empty rights = reconnect bot with Reply/Read.
    print(
        "business_connection:",
        {
            "id": conn.get("id"),
            "is_enabled": conn.get("is_enabled"),
            "can_reply": conn.get("can_reply"),
            "can_read_messages": conn.get("can_read_messages"),
            "rights": conn.get("rights"),
            "owner": conn.get("user_username"),
        },
    )


def test_live_resolve_and_send_to_papa2139(live_ready):
    """Resolve target + sendMessage via business_connection_id."""
    nick = (
        os.getenv("MOYSKLAD_TELEGRAM_TEST_USERNAME") or "papa2139"
    ).strip().lstrip("@")
    text = (
        os.getenv("MOYSKLAD_TELEGRAM_TEST_TEXT")
        or f"Hermes live test: Business bot → @{nick}"
    ).strip()
    numeric = (os.getenv("MOYSKLAD_TELEGRAM_TEST_CHAT_ID") or "").strip()

    conn = tg.fetch_business_connection()
    assert conn.get("ok") is True, conn
    if not conn.get("can_reply"):
        pytest.fail(
            "Business connection has can_reply=false / empty rights. "
            "In Telegram: Settings → Business → Chatbots → "
            "@BoberSystemsAssistant_bot → enable Reply to messages "
            "(and Read messages if needed), then re-run."
            f" connection={conn}"
        )

    chat_ref = numeric or f"@{nick}"
    coerced = tg.coerce_business_chat_id(chat_ref)
    if not coerced.get("ok"):
        pytest.fail(
            "Cannot resolve integer chat_id for Business send. "
            "Bot API rejects @username when business_connection_id is set, "
            "and getChat only works after a prior private chat / business_message. "
            f"Ask @{nick} to write to the business account once, or set "
            "MOYSKLAD_TELEGRAM_TEST_CHAT_ID=<numeric id from business_message.chat.id>. "
            f"detail={coerced}"
        )

    chat_id = str(coerced["chat_id"])
    print("resolved chat_id:", chat_id, "via", coerced.get("resolved_via"))

    out = tg.send_telegram_message(
        text=text,
        chat_id=chat_id,
        business_connection_id=conn["id"],
    )
    assert out.get("ok") is True, out
    assert out.get("message_id"), out
    print("sent:", out)
