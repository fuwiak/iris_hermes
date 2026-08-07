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
# Client-card heuristic: real Telegram user ids are long; keep floor for resolve.
_TG_CHAT_ID_RE = re.compile(r"^-?\d{5,20}$")
# After getChat / explicit numeric override, accept any integer peer id.
_TG_PEER_ID_RE = re.compile(r"^-?\d{1,20}$")
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


def telegram_api(
    method: str,
    *,
    token: str | None = None,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Call Bot API method. Returns parsed JSON ``{ok, ...}``; never raises for API errors."""
    token = (token or outreach_bot_token()).strip()
    method = (method or "").strip().lstrip("/")
    if not token:
        return {
            "ok": False,
            "error": "telegram_token_missing",
            "detail": "Set MOYSKLAD_TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN",
        }
    if not method:
        return {"ok": False, "error": "telegram_method_missing", "detail": "method required"}

    url = f"{_API}/bot{token}/{method}"
    try:
        with httpx.Client(timeout=timeout) as client:
            if json_body is not None:
                resp = client.post(url, json=json_body)
            else:
                resp = client.post(url, params=params or {})
            data = resp.json() if resp.content else {}
    except Exception as exc:  # pragma: no cover - network
        log.warning("moysklad telegram %s failed: %s", method, exc)
        return {"ok": False, "error": "telegram_network", "detail": str(exc)}

    if not isinstance(data, dict):
        return {"ok": False, "error": "telegram_bad_response", "detail": str(data)}
    if data.get("ok"):
        return data
    return {
        "ok": False,
        "error": "telegram_api",
        "detail": str(data.get("description") or resp.text or f"{method} failed"),
        "error_code": data.get("error_code"),
        "raw": data,
    }


def fetch_bot_identity(token: str | None = None) -> dict[str, Any]:
    """GET getMe — verify outreach bot identity."""
    data = telegram_api("getMe", token=token)
    if not data.get("ok"):
        return data
    result = data.get("result") or {}
    return {
        "ok": True,
        "id": result.get("id"),
        "username": result.get("username"),
        "first_name": result.get("first_name"),
        "can_join_groups": result.get("can_join_groups"),
        "can_read_all_group_messages": result.get("can_read_all_group_messages"),
    }


def fetch_business_connection(
    business_connection_id: str | None = None,
    *,
    token: str | None = None,
) -> dict[str, Any]:
    """Pull BusinessConnection via getBusinessConnection (rights / can_reply)."""
    biz = (business_connection_id or resolve_business_connection_id()).strip()
    if not biz:
        return {
            "ok": False,
            "error": "business_connection_missing",
            "detail": "Set MOYSKLAD_TELEGRAM_BUSINESS_CONNECTION_ID",
        }
    data = telegram_api(
        "getBusinessConnection",
        token=token,
        params={"business_connection_id": biz},
    )
    if not data.get("ok"):
        return data
    result = data.get("result") or {}
    rights = result.get("rights") or {}
    # Bot API keeps legacy top-level can_reply alongside rights.can_reply.
    can_reply = bool(result.get("can_reply") or rights.get("can_reply"))
    can_read = bool(rights.get("can_read_messages"))
    user = result.get("user") or {}
    return {
        "ok": True,
        "id": result.get("id") or biz,
        "is_enabled": bool(result.get("is_enabled")),
        "can_reply": can_reply,
        "can_read_messages": can_read,
        "rights": rights,
        "user_chat_id": result.get("user_chat_id"),
        "user_username": user.get("username"),
        "user_first_name": user.get("first_name"),
        "date": result.get("date"),
    }


def fetch_chat(chat_id: str, *, token: str | None = None) -> dict[str, Any]:
    """Resolve chat via getChat. Works for known peers; @username needs prior contact."""
    chat_id = str(chat_id or "").strip()
    if not chat_id:
        return {"ok": False, "error": "telegram_chat_missing", "detail": "chat_id required"}
    data = telegram_api("getChat", token=token, params={"chat_id": chat_id})
    if not data.get("ok"):
        return data
    result = data.get("result") or {}
    return {
        "ok": True,
        "id": result.get("id"),
        "type": result.get("type"),
        "username": result.get("username"),
        "first_name": result.get("first_name"),
        "last_name": result.get("last_name"),
    }


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


def coerce_business_chat_id(
    chat_id: str,
    *,
    token: str | None = None,
) -> dict[str, Any]:
    """Business sendMessage requires integer chat_id (not @username).

    Tries getChat when given @nick / t.me link. Cold outreach without prior
    business_message history cannot resolve a private user id via Bot API.
    """
    raw = str(chat_id or "").strip()
    if not raw:
        return {"ok": False, "error": "telegram_chat_missing", "detail": "chat_id required"}
    if _TG_PEER_ID_RE.fullmatch(raw):
        return {"ok": True, "chat_id": raw, "resolved_via": "numeric"}

    nick = raw
    if nick.startswith("@"):
        pass
    else:
        m = _TME_RE.search(nick)
        nick = f"@{m.group(1)}" if m else f"@{normalize_tg_nick(nick)}"
    if not nick or nick == "@":
        return {
            "ok": False,
            "error": "telegram_chat_unresolved",
            "detail": "Need numeric chat id for Business send",
        }

    chat = fetch_chat(nick, token=token)
    if chat.get("ok") and chat.get("id") is not None:
        return {
            "ok": True,
            "chat_id": str(chat["id"]),
            "resolved_via": "getChat",
            "username": chat.get("username"),
        }
    return {
        "ok": False,
        "error": "telegram_chat_unresolved",
        "detail": (
            chat.get("detail")
            or "Business send needs integer chat_id from a prior business_message "
            "(Bot API cannot resolve cold @username). Set MOYSKLAD_TELEGRAM_TEST_CHAT_ID "
            "or store numeric id on the client card."
        ),
        "username": nick.lstrip("@"),
        "cause": chat,
    }


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

    if biz and not _TG_PEER_ID_RE.fullmatch(chat_id):
        coerced = coerce_business_chat_id(chat_id, token=token)
        if not coerced.get("ok"):
            return coerced
        chat_id = str(coerced["chat_id"])

    payload: dict[str, Any] = {
        "chat_id": int(chat_id) if _TG_PEER_ID_RE.fullmatch(chat_id) else chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if biz:
        payload["business_connection_id"] = biz

    data = telegram_api(
        "sendMessage",
        token=token,
        json_body=payload,
        timeout=timeout,
    )
    if not data.get("ok"):
        return data
    result = data.get("result") or {}
    return {
        "ok": True,
        "message_id": result.get("message_id"),
        "chat_id": (result.get("chat") or {}).get("id") or chat_id,
        "business_connection_id": biz or None,
        "bot_username": outreach_bot_username() or None,
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
        "business_connection_id": resolve_business_connection_id() or None,
    }


def telegram_account_snapshot(
    business_connection_id: str | None = None,
    *,
    token: str | None = None,
    probe: bool = True,
) -> dict[str, Any]:
    """UI-facing Telegram Business account block (no secrets).

    When ``probe`` is true and a connection id is known, calls
    ``getBusinessConnection`` so the campaigns page can show @username /
    can_reply without exposing the bot token.
    """
    status = telegram_send_status()
    biz = (business_connection_id or resolve_business_connection_id() or "").strip()
    out: dict[str, Any] = {
        **status,
        "business_connection_id": biz or None,
        "account": None,
    }
    if not probe or not biz or not outreach_bot_token():
        return out
    conn = fetch_business_connection(biz, token=token)
    if not conn.get("ok"):
        out["account"] = {
            "ok": False,
            "error": conn.get("error"),
            "detail": conn.get("detail"),
        }
        return out
    out["account"] = {
        "ok": True,
        "id": conn.get("id"),
        "is_enabled": conn.get("is_enabled"),
        "can_reply": conn.get("can_reply"),
        "can_read_messages": conn.get("can_read_messages"),
        "username": conn.get("user_username") or None,
        "first_name": conn.get("user_first_name") or None,
        "user_chat_id": conn.get("user_chat_id"),
        "rights": conn.get("rights") or {},
    }
    return out
