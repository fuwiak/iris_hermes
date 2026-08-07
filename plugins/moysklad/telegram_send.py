"""Outbound Telegram send for MoySklad Рассылки.

Two channels, picked by ``MOYSKLAD_TELEGRAM_SEND_VIA`` (auto | user | bot):

* **user** — the operator's own Telegram account over MTProto
  (``plugins.platforms.telegram_user``). Only this one can message a contact
  who never wrote first, and only this one sees the personal contact list.
* **bot** — Bot API ``sendMessage`` through the Telegram Business connection
  (Office → Telegram Business), see ``plugins.platforms.telegram_business``.

``auto`` (default) prefers the connected personal account and falls back to
the Business bot. Does not touch the bot webhook (gateway may own updates).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

from plugins.moysklad.conversations import normalize_tg_nick
from plugins.platforms.telegram_business.client import (
    business_bot_token as outreach_bot_token,
    business_bot_username as outreach_bot_username,
    fetch_bot_identity,
    fetch_business_connection,
    resolve_business_connection_id,
    telegram_account_snapshot,
    telegram_api,
    telegram_send_status,
)
from plugins.platforms.telegram_user import client as tg_user

log = logging.getLogger(__name__)

# Client-card heuristic: real Telegram user ids are long; keep floor for resolve.
_TG_CHAT_ID_RE = re.compile(r"^-?\d{5,20}$")
# After getChat / explicit numeric override, accept any integer peer id.
_TG_PEER_ID_RE = re.compile(r"^-?\d{1,20}$")
_TME_RE = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z0-9_]{4,64})",
    re.IGNORECASE,
)


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


def _lookup_peer_in_local_stores(
    *,
    nick: str = "",
    chat_id: str = "",
) -> dict[str, Any] | None:
    """Best-effort resolve from export overlay / custom contacts / conversations."""
    nick = normalize_tg_nick(nick)
    chat_id = str(chat_id or "").strip()

    cached = tg_user.find_cached_contact(tg_nick=nick, tg_chat_id=chat_id)
    if cached:
        return {
            "tg_nick": cached.get("tg_nick") or nick,
            "tg_chat_id": str(cached.get("tg_chat_id") or ""),
            "name": cached.get("name") or "",
            "resolved_via": "telegram_contacts",
        }

    try:
        from plugins.moysklad.outreach_contacts import load_custom_contacts
        from plugins.moysklad.telegram_export import load_overlay

        for c in load_custom_contacts():
            if nick and c.get("tg_nick") == nick:
                return {
                    "tg_nick": c.get("tg_nick") or nick,
                    "tg_chat_id": str(c.get("tg_chat_id") or ""),
                    "name": c.get("name") or "",
                    "resolved_via": "custom_store",
                }
            if chat_id and str(c.get("tg_chat_id") or "") == chat_id:
                return {
                    "tg_nick": c.get("tg_nick") or "",
                    "tg_chat_id": chat_id,
                    "name": c.get("name") or "",
                    "resolved_via": "custom_store",
                }

        overlay = load_overlay()
        by_client = overlay.get("by_client_id") or {}
        if isinstance(by_client, dict):
            for entry in by_client.values():
                if not isinstance(entry, dict):
                    continue
                entry_nick = normalize_tg_nick(entry.get("tg_nick") or "")
                entry_chat = str(entry.get("tg_chat_id") or "").strip()
                if nick and entry_nick == nick:
                    return {
                        "tg_nick": entry_nick or nick,
                        "tg_chat_id": entry_chat,
                        "name": entry.get("chat_name") or entry.get("name") or "",
                        "resolved_via": "export_overlay",
                    }
                if chat_id and entry_chat == chat_id:
                    return {
                        "tg_nick": entry_nick,
                        "tg_chat_id": chat_id,
                        "name": entry.get("chat_name") or entry.get("name") or "",
                        "resolved_via": "export_overlay",
                    }
    except Exception:
        log.debug("local peer lookup failed", exc_info=True)
    return None


def resolve_peer_identity(
    *,
    tg_nick: str = "",
    tg_chat_id: str = "",
    query: str = "",
    token: str | None = None,
) -> dict[str, Any]:
    """Resolve @nick / t.me / numeric id → {tg_nick, tg_chat_id, name}.

    Order: parse query → local stores (export/custom) → Bot API getChat.
    """
    raw_query = str(query or "").strip()
    nick = normalize_tg_nick(tg_nick)
    chat_id = str(tg_chat_id or "").strip()

    if raw_query:
        if _TG_PEER_ID_RE.fullmatch(raw_query):
            chat_id = chat_id or raw_query
        else:
            m = _TME_RE.search(raw_query)
            if m:
                nick = nick or normalize_tg_nick(m.group(1))
            elif raw_query.startswith("tg://user?id="):
                digits = raw_query.split("id=", 1)[-1].strip()
                if _TG_PEER_ID_RE.fullmatch(digits):
                    chat_id = chat_id or digits
            else:
                nick = nick or normalize_tg_nick(raw_query)

    if not nick and not chat_id:
        return {
            "ok": False,
            "error": "peer_missing",
            "detail": "Укажите @ник, t.me/… или numeric chat id",
        }

    local = _lookup_peer_in_local_stores(nick=nick, chat_id=chat_id)
    if local and (local.get("tg_chat_id") or local.get("tg_nick")):
        # Prefer filling missing side from local, then still try getChat to enrich.
        nick = nick or str(local.get("tg_nick") or "")
        chat_id = chat_id or str(local.get("tg_chat_id") or "")
        name_hint = str(local.get("name") or "")
        via = str(local.get("resolved_via") or "local")
    else:
        name_hint = ""
        via = ""

    # Personal account resolves cold @nicks the bot has never seen.
    if not chat_id and tg_user.is_authorized():
        mt = tg_user.resolve_peer(chat_id or f"@{nick}")
        if mt.get("ok") and mt.get("tg_chat_id"):
            return {
                "ok": True,
                "tg_nick": normalize_tg_nick(mt.get("tg_nick") or nick),
                "tg_chat_id": str(mt["tg_chat_id"]),
                "name": str(mt.get("name") or name_hint or ""),
                "resolved_via": "mtproto",
            }

    # Bot API getChat — works for known peers / public usernames the bot can see.
    lookup = f"@{nick}" if nick and not chat_id else (chat_id or f"@{nick}")
    chat = fetch_chat(lookup, token=token)
    if chat.get("ok") and chat.get("id") is not None:
        resolved_nick = normalize_tg_nick(chat.get("username") or nick)
        first = str(chat.get("first_name") or "").strip()
        last = str(chat.get("last_name") or "").strip()
        full = " ".join(p for p in (first, last) if p)
        return {
            "ok": True,
            "tg_nick": resolved_nick,
            "tg_chat_id": str(chat["id"]),
            "name": name_hint or full,
            "resolved_via": "getChat",
            "chat_type": chat.get("type"),
        }

    if chat_id and _TG_PEER_ID_RE.fullmatch(chat_id):
        # Numeric id alone is enough for Business send even without nick.
        return {
            "ok": True,
            "tg_nick": nick,
            "tg_chat_id": chat_id,
            "name": name_hint,
            "resolved_via": via or "numeric",
            "warning": chat.get("detail") or None,
        }

    if nick and local and local.get("tg_chat_id"):
        return {
            "ok": True,
            "tg_nick": nick,
            "tg_chat_id": str(local["tg_chat_id"]),
            "name": name_hint,
            "resolved_via": via or "local",
        }

    return {
        "ok": False,
        "error": "telegram_chat_unresolved",
        "detail": (
            chat.get("detail")
            or "Bot API не знает этот чат (getChat). Нужен numeric chat id "
            "из business_message / Telegram export, либо пусть контакт напишет "
            "на Business-аккаунт."
        ),
        "tg_nick": nick or None,
        "tg_chat_id": chat_id or None,
        "cause": chat,
    }


def _send_via_user_account(*, text: str, chat_id: str) -> dict[str, Any] | None:
    """Send from the operator's own account. ``None`` when it isn't connected."""
    peer = str(chat_id or "").strip()
    if not peer:
        return None
    try:
        if not tg_user.is_authorized():
            return None
        result = tg_user.send_message(peer=peer, text=text)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("telegram user send crashed: %s", exc)
        return {"ok": False, "error": "telegram_user_error", "detail": str(exc)}
    if result.get("ok"):
        user = tg_user.load_config().get("user") or {}
        return {
            "ok": True,
            "message_id": result.get("message_id"),
            "chat_id": result.get("chat_id") or peer,
            "via": "user_account",
            "user_username": user.get("username"),
        }
    return result


def telegram_send_mode() -> str:
    """``auto`` | ``user`` | ``bot`` — which channel outreach sends through."""
    mode = (os.getenv("MOYSKLAD_TELEGRAM_SEND_VIA") or "auto").strip().lower()
    return mode if mode in {"auto", "user", "bot"} else "auto"


def telegram_user_status(*, probe: bool = True) -> dict[str, Any]:
    """Personal-account block for the UI (no secrets)."""
    try:
        return tg_user.user_status(probe=probe)
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("telegram_user status failed", exc_info=True)
        return {"ok": False, "available": False, "detail": str(exc)}


def send_telegram_message(
    *,
    text: str,
    chat_id: str,
    business_connection_id: Optional[str] = None,
    token: str | None = None,
    timeout: float = 30.0,
    via: str = "",
) -> dict[str, Any]:
    """Send outreach. Returns ``{ok, ...}``; never raises for API errors.

    ``via`` overrides ``MOYSKLAD_TELEGRAM_SEND_VIA`` for one call.
    """
    mode = (via or telegram_send_mode()).strip().lower()
    if mode not in {"auto", "user", "bot"}:
        mode = "auto"

    if mode in {"auto", "user"}:
        user_result = _send_via_user_account(text=text, chat_id=chat_id)
        if user_result is not None and user_result.get("ok"):
            return user_result
        if mode == "user":
            return user_result or {
                "ok": False,
                "error": "telegram_user_unavailable",
                "detail": "Личный Telegram не подключён (Рассылки → Личный Telegram)",
            }
        if user_result is not None:
            log.info(
                "telegram user send failed (%s), falling back to Business bot",
                user_result.get("error"),
            )

    token = (token or outreach_bot_token()).strip()
    chat_id = str(chat_id or "").strip()
    text = (text or "").strip()
    if not token:
        return {
            "ok": False,
            "error": "telegram_token_missing",
            "detail": "Set TELEGRAM_BUSINESS_BOT_TOKEN (Office) or MOYSKLAD_TELEGRAM_BOT_TOKEN / TELEGRAM_BOT_TOKEN",
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
        "via": "business_bot",
    }


def send_outreach_to_client(
    *,
    text: str,
    tg_nick: str = "",
    tg_conversation: str = "",
    tg_chat_id: str = "",
    via: str = "",
) -> dict[str, Any]:
    """Resolve client TG target and send. Returns send_telegram_message result."""
    chat_id = resolve_telegram_chat_id(
        tg_nick=tg_nick,
        tg_conversation=tg_conversation,
        tg_chat_id=tg_chat_id,
    )
    return send_telegram_message(text=text, chat_id=chat_id, via=via)