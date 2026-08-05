"""Outbound Telegram send for MoySklad Рассылки (Business bot).

Uses Bot API ``sendMessage``. When ``MOYSKLAD_TELEGRAM_BUSINESS_CONNECTION_ID``
(or seller_settings) is set, messages go on behalf of the Telegram Business
account. Token defaults to ``MOYSKLAD_TELEGRAM_BOT_TOKEN``, then
``TELEGRAM_BOT_TOKEN``.

Does not touch the bot webhook (Railway / gateway may own updates).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

import httpx

from plugins.moysklad.campaigns import get_seller_settings
from plugins.moysklad.conversations import normalize_tg_nick

log = logging.getLogger(__name__)

_API = "https://api.telegram.org"
_TG_CHAT_ID_RE = re.compile(r"^-?\d{5,20}$")
_TME_RE = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z0-9_]{4,64})",
    re.IGNORECASE,
)


def outreach_bot_token() -> str:
    return (
        (os.getenv("MOYSKLAD_TELEGRAM_BOT_TOKEN") or "").strip()
        or (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    )


def outreach_bot_username() -> str:
    return (
        (os.getenv("MOYSKLAD_TELEGRAM_BOT_USERNAME") or "").strip().lstrip("@")
        or (os.getenv("TELEGRAM_BOT_USERNAME") or "").strip().lstrip("@")
    )


def resolve_business_connection_id() -> str:
    env_id = (os.getenv("MOYSKLAD_TELEGRAM_BUSINESS_CONNECTION_ID") or "").strip()
    if env_id:
        return env_id
    stored = get_seller_settings().get("telegram_business_connection_id") or ""
    return str(stored).strip()


def resolve_telegram_chat_id(
    *,
    tg_nick: str = "",
    tg_conversation: str = "",
    tg_chat_id: str = "",
) -> str:
    """Return Bot API chat_id (@username or numeric). Empty if unresolvable."""
    raw_id = str(tg_chat_id or "").strip()
    if _TG_CHAT_ID_RE.fullmatch(raw_id):
        return raw_id

    conv = str(tg_conversation or "").strip()
    if _TG_CHAT_ID_RE.fullmatch(conv):
        return conv
    if conv.lower().startswith("tg://user?id="):
        digits = conv.split("id=", 1)[-1].strip()
        if _TG_CHAT_ID_RE.fullmatch(digits):
            return digits
    m = _TME_RE.search(conv)
    if m:
        return f"@{m.group(1)}"

    nick = normalize_tg_nick(tg_nick)
    if nick:
        return f"@{nick}"
    return ""


def send_telegram_message(
    *,
    text: str,
    chat_id: str,
    business_connection_id: Optional[str] = None,
    token: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """POST sendMessage. Returns ``{ok, ...}``; never raises for API errors."""
    token = (token or outreach_bot_token()).strip()
    chat_id = str(chat_id or "").strip()
    text = (text or "").strip()
    if not token:
        return {
            "ok": False,
            "error": "telegram_token_missing",
            "detail": "Set MOYSKLAD_TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN",
        }
    if not chat_id:
        return {
            "ok": False,
            "error": "telegram_chat_missing",
            "detail": "Client needs ТГ ник / chat id",
        }
    if not text:
        return {"ok": False, "error": "empty_text", "detail": "message required"}

    if business_connection_id is None:
        biz = resolve_business_connection_id()
    else:
        biz = str(business_connection_id).strip()

    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if biz:
        payload["business_connection_id"] = biz

    url = f"{_API}/bot{token}/sendMessage"
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload)
            data = resp.json() if resp.content else {}
    except Exception as exc:  # pragma: no cover - network
        log.warning("moysklad telegram send failed: %s", exc)
        return {"ok": False, "error": "telegram_network", "detail": str(exc)}

    if not isinstance(data, dict):
        return {"ok": False, "error": "telegram_bad_response", "detail": str(data)}
    if data.get("ok"):
        result = data.get("result") or {}
        return {
            "ok": True,
            "message_id": result.get("message_id"),
            "chat_id": (result.get("chat") or {}).get("id") or chat_id,
            "business_connection_id": biz or None,
            "bot_username": outreach_bot_username() or None,
        }
    desc = str(data.get("description") or resp.text or "sendMessage failed")
    log.info("moysklad telegram sendMessage rejected: %s", desc)
    return {
        "ok": False,
        "error": "telegram_api",
        "detail": desc,
        "error_code": data.get("error_code"),
    }


def send_outreach_to_client(
    *,
    text: str,
    tg_nick: str = "",
    tg_conversation: str = "",
    tg_chat_id: str = "",
) -> dict[str, Any]:
    """Resolve client TG target and send. Returns send_telegram_message result."""
    chat_id = resolve_telegram_chat_id(
        tg_nick=tg_nick,
        tg_conversation=tg_conversation,
        tg_chat_id=tg_chat_id,
    )
    return send_telegram_message(text=text, chat_id=chat_id)


def telegram_send_status() -> dict[str, Any]:
    """Non-secret status for UI / health."""
    token = outreach_bot_token()
    return {
        "configured": bool(token),
        "bot_username": outreach_bot_username() or None,
        "business_connection_configured": bool(resolve_business_connection_id()),
    }
