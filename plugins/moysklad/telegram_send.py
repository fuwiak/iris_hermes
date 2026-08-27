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
import time
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

    local = _lookup_peer_in_local_stores(nick=nick)
    if local and str(local.get("tg_chat_id") or "").strip():
        return {
            "ok": True,
            "chat_id": str(local["tg_chat_id"]),
            "resolved_via": str(local.get("resolved_via") or "local"),
            "username": local.get("tg_nick") or nick.lstrip("@"),
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
        from plugins.moysklad.telegram_archive import find_peer as archive_peer
        from plugins.moysklad.telegram_export import load_overlay

        # The Telegram export carries a numeric peer id for every chat — the one
        # thing Bot API cannot derive from a cold @username.
        hit = archive_peer(tg_nick=nick, tg_chat_id=chat_id)
        if hit and hit.get("tg_chat_id"):
            return {
                "tg_nick": hit.get("tg_nick") or nick,
                "tg_chat_id": str(hit.get("tg_chat_id") or ""),
                "name": hit.get("name") or "",
                "resolved_via": "tg_archive",
            }

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
    # On Selectel this MUST go through TELEGRAM_USER_GATEWAY_URL (Railway) —
    # local MTProto hits Errno 101 Network is unreachable.
    if not chat_id and tg_user.is_authorized():
        mt = tg_user.resolve_peer(chat_id or f"@{nick}")
        if mt.get("ok") and mt.get("tg_chat_id"):
            return {
                "ok": True,
                "tg_nick": normalize_tg_nick(mt.get("tg_nick") or nick),
                "tg_chat_id": str(mt["tg_chat_id"]),
                "name": str(mt.get("name") or name_hint or ""),
                "resolved_via": str(mt.get("resolved_via") or "mtproto"),
            }
        # Don't fall through to Bot API when MTProto/gateway already told us
        # the network path is dead — Bot API from RU often fails the same way.
        if mt.get("network_blocked") or mt.get("error") in {
            "network_unreachable",
            "gateway_unreachable",
            "timeout",
        }:
            return {
                "ok": False,
                "error": str(mt.get("error") or "network_unreachable"),
                "detail": str(
                    mt.get("detail")
                    or "Telegram недоступен с этого сервера. Нужен TELEGRAM_USER_GATEWAY_URL."
                ),
                "tg_nick": nick or None,
                "tg_chat_id": chat_id or None,
                "cause": mt,
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


def _send_photo_via_user_account(
    *,
    text: str,
    chat_id: str,
    image_bytes: bytes | None,
    image_name: str,
    image_url: str,
) -> dict[str, Any] | None:
    """Photo from the operator's own account. ``None`` when it isn't connected.

    Long text rides as the caption up to 1024 chars; the remainder follows as
    a plain message so nothing is silently truncated.
    """
    peer = str(chat_id or "").strip()
    if not peer:
        return None
    try:
        if not tg_user.is_authorized():
            return None
        result = tg_user.send_photo(
            peer=peer,
            caption=text or "",
            image_bytes=image_bytes,
            image_name=image_name or "photo.jpg",
            image_url=image_url or "",
        )
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("telegram user photo send crashed: %s", exc)
        return {"ok": False, "error": "telegram_user_error", "detail": str(exc)}
    if not result.get("ok"):
        return result

    out = {
        "ok": True,
        "message_id": result.get("message_id"),
        "chat_id": result.get("chat_id") or peer,
        "via": "user_account_photo",
    }
    body = (text or "").strip()
    if len(body) > 1024:
        tail = _send_via_user_account(text=body[1024:], chat_id=peer)
        out["tail_ok"] = bool(tail and tail.get("ok"))
    return out


def _retry_after_seconds(data: dict[str, Any]) -> float:
    """Seconds Telegram asks us to wait on a 429, ``0`` when it is not a flood wait."""
    if data.get("error_code") != 429:
        return 0.0
    raw = data.get("raw")
    params = (raw or {}).get("parameters") if isinstance(raw, dict) else None
    try:
        return float((params or {}).get("retry_after") or 0)
    except (TypeError, ValueError):
        return 0.0


def send_flood_wait_max() -> float:
    """Longest single flood wait we sit through before giving the row back."""
    raw = (os.getenv("MOYSKLAD_TELEGRAM_FLOOD_WAIT_MAX") or "").strip()
    try:
        return max(0.0, float(raw)) if raw else 60.0
    except ValueError:
        return 60.0


def _send_with_flood_wait(
    payload: dict[str, Any],
    *,
    token: str,
    timeout: float,
    attempts: int = 3,
) -> dict[str, Any]:
    """``sendMessage`` that honours Telegram's 429 ``retry_after`` instead of dropping."""
    ceiling = send_flood_wait_max()
    data: dict[str, Any] = {}
    for attempt in range(max(1, attempts)):
        data = telegram_api("sendMessage", token=token, json_body=payload, timeout=timeout)
        if data.get("ok"):
            return data
        wait = _retry_after_seconds(data)
        if wait <= 0 or wait > ceiling or attempt == attempts - 1:
            if wait > 0:
                data = dict(data)
                data["retry_after"] = wait
            return data
        log.info("telegram flood wait %.1fs (attempt %s)", wait, attempt + 1)
        time.sleep(wait)
    return data


def send_delay_seconds() -> float:
    """Pause between messages in a batch — keeps bulk sends under Bot API limits."""
    raw = (os.getenv("MOYSKLAD_TELEGRAM_SEND_DELAY_MS") or "").strip()
    try:
        ms = float(raw) if raw else 350.0
    except ValueError:
        ms = 350.0
    return max(0.0, min(ms, 10_000.0)) / 1000.0


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


def _decode_image(image_base64: str) -> bytes | None:
    """data:-URL or bare base64 → bytes (None when empty/broken)."""
    raw = (image_base64 or "").strip()
    if not raw:
        return None
    if raw.startswith("data:"):
        raw = raw.split(",", 1)[-1]
    try:
        import base64

        return base64.b64decode(raw, validate=False)
    except Exception:
        return None


def _normalize_image_url(image_url: str) -> str:
    """Browsers accept ``//cdn/…``; Telegram / our gate need an absolute URL."""
    url = (image_url or "").strip()
    if url.startswith("//"):
        return f"https:{url}"
    return url


def _download_image_url(image_url: str, *, timeout: float = 30.0) -> bytes | None:
    """Fetch a remote card photo so we upload bytes (CDN hotlink-safe)."""
    url = _normalize_image_url(image_url)
    if not url.startswith(("http://", "https://")):
        return None
    try:
        import urllib.request

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "HermesMoySklad/1.0 (+https://github.com/NousResearch/hermes-agent)",
                "Accept": "image/*,*/*;q=0.8",
            },
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(10 * 1024 * 1024 + 1)
        if not data or len(data) > 10 * 1024 * 1024:
            return None
        return data
    except Exception as exc:
        log.info("card image download failed (%s): %s", url[:120], exc)
        return None


def _resolve_send_image(
    image_base64: str,
    image_url: str,
) -> tuple[bytes | None, str, bool]:
    """Return ``(bytes, url, wanted)``. Prefer uploaded bytes over a remote URL.

    ``wanted`` is True when the caller attached something — so a broken URL
    must not silently fall through to a text-only send.
    """
    raw_b64 = (image_base64 or "").strip()
    raw_url = _normalize_image_url(image_url)
    wanted = bool(raw_b64 or raw_url)

    if raw_url.startswith("data:"):
        return _decode_image(raw_url), "", wanted

    image_bytes = _decode_image(raw_b64)
    if image_bytes:
        return image_bytes, "", wanted

    if raw_url.startswith(("http://", "https://")):
        fetched = _download_image_url(raw_url)
        if fetched:
            return fetched, "", wanted
        # Telegram may still fetch the URL itself (Bot API sendPhoto photo=url).
        return None, raw_url, wanted

    return None, "", wanted


def send_telegram_message(
    *,
    text: str,
    chat_id: str,
    business_connection_id: Optional[str] = None,
    token: str | None = None,
    timeout: float = 30.0,
    via: str = "",
    image_base64: str = "",
    image_name: str = "photo.jpg",
    image_url: str = "",
) -> dict[str, Any]:
    """Send outreach. Returns ``{ok, ...}``; never raises for API errors.

    ``via`` overrides ``MOYSKLAD_TELEGRAM_SEND_VIA`` for one call.
    ``image_base64`` attaches an uploaded photo, ``image_url`` a remote one
    (e.g. a marketplace card image — we download it when possible, else
    Telegram fetches the URL). Photos prefer the personal MTProto account
    (Business bot cannot message cold contacts); text rides as the caption
    (split into a follow-up message when longer than 1024).
    """
    mode = (via or telegram_send_mode()).strip().lower()
    if mode not in {"auto", "user", "bot"}:
        mode = "auto"

    image_bytes, resolved_url, image_wanted = _resolve_send_image(
        image_base64, image_url
    )
    if image_bytes or resolved_url.startswith(("http://", "https://")):
        # Personal account first: the Business bot can only reply inside chats
        # of the connected account, so a photo to a plain contact comes back
        # as chat not found / BUSINESS_PEER_INVALID and never arrives.
        if mode in {"auto", "user"}:
            user_photo = _send_photo_via_user_account(
                text=text,
                chat_id=chat_id,
                image_bytes=image_bytes,
                image_name=image_name or "photo.jpg",
                image_url=resolved_url,
            )
            if user_photo is not None and user_photo.get("ok"):
                return user_photo
            if mode == "user":
                return user_photo or {
                    "ok": False,
                    "error": "telegram_user_unavailable",
                    "detail": "Личный Telegram не подключён (Рассылки → Личный Telegram)",
                }
            if user_photo is not None:
                log.info(
                    "telegram user photo send failed (%s), falling back to Business bot",
                    user_photo.get("error"),
                )

        return _send_photo_via_bot(
            text=text,
            chat_id=chat_id,
            image_bytes=image_bytes,
            image_name=image_name or "photo.jpg",
            image_url=resolved_url,
            business_connection_id=business_connection_id,
            token=token,
            timeout=max(timeout, 60.0),
        )

    if image_wanted:
        return {
            "ok": False,
            "error": "image_unusable",
            "detail": (
                "Фото карточки не удалось отправить: нужен http(s) URL или "
                "загрузка файла. Проверьте ссылку на картинку маркетплейса."
            ),
        }

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

    data = _send_with_flood_wait(payload, token=token, timeout=timeout)
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


def preflight_recipient(
    *,
    tg_nick: str = "",
    tg_conversation: str = "",
    tg_chat_id: str = "",
    token: str | None = None,
) -> dict[str, Any]:
    """Can the Business bot actually deliver to this peer? Answer before sending.

    Business ``sendMessage`` needs an integer chat id; a bare ``@nick`` the bot
    has never seen is not deliverable. Resolving up front turns a silent
    half-failed рассылка into a list you can fix.
    """
    raw = resolve_telegram_chat_id(
        tg_nick=tg_nick,
        tg_conversation=tg_conversation,
        tg_chat_id=tg_chat_id,
    )
    if not raw:
        return {
            "ok": False,
            "error": "telegram_chat_missing",
            "detail": "Нет ни ТГ ника, ни chat id",
        }
    if _TG_PEER_ID_RE.fullmatch(raw):
        return {"ok": True, "chat_id": raw, "resolved_via": "numeric"}
    coerced = coerce_business_chat_id(raw, token=token)
    if coerced.get("ok"):
        return coerced
    return {
        "ok": False,
        "error": coerced.get("error") or "telegram_chat_unresolved",
        "detail": coerced.get("detail") or "",
        "tg_nick": normalize_tg_nick(tg_nick) or None,
    }


def business_preflight(token: str | None = None) -> dict[str, Any]:
    """Account-level readiness: bot token + business connection with reply rights."""
    status = telegram_send_status()
    if not status.get("configured"):
        return {
            "ok": False,
            "error": "telegram_token_missing",
            "detail": "Нет токена бота (Офис → Telegram Business)",
            **status,
        }
    snapshot = telegram_account_snapshot(token=token, probe=True)
    account = snapshot.get("account") or {}
    if not account.get("ok"):
        return {
            "ok": False,
            "error": account.get("error") or "business_connection_missing",
            "detail": account.get("detail") or "Business connection не подключён",
            **snapshot,
        }
    if not account.get("can_reply"):
        return {
            "ok": False,
            "error": "business_cannot_reply",
            "detail": "У бота нет права Reply в Telegram Business — включите его в настройках Telegram",
            **snapshot,
        }
    return {"ok": True, **snapshot}


def _send_photo_via_bot(
    *,
    text: str,
    chat_id: str,
    image_bytes: bytes | None = None,
    image_name: str = "photo.jpg",
    image_url: str = "",
    business_connection_id: Optional[str] = None,
    token: str | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """``sendPhoto`` through the Business bot (multipart upload or URL)."""
    token = (token or outreach_bot_token()).strip()
    chat_id = str(chat_id or "").strip()
    if not token:
        return {
            "ok": False,
            "error": "telegram_token_missing",
            "detail": (
                "Картинки идут через Business-бота — нужен "
                "TELEGRAM_BUSINESS_BOT_TOKEN / MOYSKLAD_TELEGRAM_BOT_TOKEN"
            ),
        }
    if not chat_id:
        return {"ok": False, "error": "telegram_chat_missing", "detail": "Client needs ТГ ник / chat id"}
    if image_bytes and len(image_bytes) > 10 * 1024 * 1024:
        return {"ok": False, "error": "image_too_large", "detail": "Фото больше 10 МБ"}
    if not image_bytes and not image_url:
        return {"ok": False, "error": "image_missing", "detail": "Нет фото (bytes/url)"}

    if business_connection_id is None:
        biz = resolve_business_connection_id()
    else:
        biz = str(business_connection_id).strip()
    if biz and not _TG_PEER_ID_RE.fullmatch(chat_id):
        coerced = coerce_business_chat_id(chat_id, token=token)
        if not coerced.get("ok"):
            return coerced
        chat_id = str(coerced["chat_id"])

    text = (text or "").strip()
    caption = text[:1024]
    payload: dict[str, Any] = {"chat_id": chat_id}
    if caption:
        payload["caption"] = caption
    if biz:
        payload["business_connection_id"] = biz
    if image_bytes:
        data = telegram_api(
            "sendPhoto",
            token=token,
            json_body=payload,
            files={"photo": (image_name, image_bytes)},
            timeout=timeout,
        )
    else:
        payload["photo"] = image_url
        data = telegram_api("sendPhoto", token=token, json_body=payload, timeout=timeout)
    if not data.get("ok"):
        return data
    result = data.get("result") or {}
    out = {
        "ok": True,
        "message_id": result.get("message_id"),
        "chat_id": (result.get("chat") or {}).get("id") or chat_id,
        "business_connection_id": biz or None,
        "via": "business_bot_photo",
    }
    # Caption tops out at 1024 — deliver the remainder as a normal message.
    if len(text) > 1024:
        tail = send_telegram_message(
            text=text[1024:],
            chat_id=chat_id,
            business_connection_id=biz or None,
            token=token,
            via="bot",
        )
        out["tail_ok"] = bool(tail.get("ok"))
    return out


MAX_OUTREACH_PHOTOS = 10


def normalize_image_attachments(
    images: Any = None,
    image_base64: str = "",
    image_name: str = "photo.jpg",
    image_url: str = "",
) -> list[dict[str, str]]:
    """Fold the photo tray (plus the legacy single-photo fields) into one list.

    Callers may send ``images=[{url|base64|name}, …]`` and/or the older
    ``image_url`` / ``image_base64`` pair. Duplicates are dropped so a client
    never receives the same picture twice, and the list is capped — Telegram
    albums hold 10.
    """
    out: list[dict[str, str]] = []
    seen: set[str] = set()

    def _push(url: str, b64: str, name: str) -> None:
        url = _normalize_image_url(url)
        b64 = (b64 or "").strip()
        if url.startswith("data:"):
            url, b64 = "", url
        if not url and not b64:
            return
        key = url or b64[:256]
        if key in seen:
            return
        seen.add(key)
        out.append(
            {
                "image_url": url,
                "image_base64": b64,
                "image_name": (name or "").strip() or "photo.jpg",
            }
        )

    for item in list(images or []):
        if isinstance(item, str):
            _push(item, "", "photo.jpg")
        elif isinstance(item, dict):
            _push(
                str(item.get("image_url") or item.get("url") or ""),
                str(item.get("image_base64") or item.get("base64") or ""),
                str(item.get("image_name") or item.get("name") or ""),
            )
        else:  # pydantic model
            _push(
                str(getattr(item, "image_url", "") or getattr(item, "url", "") or ""),
                str(getattr(item, "image_base64", "") or getattr(item, "base64", "") or ""),
                str(getattr(item, "image_name", "") or getattr(item, "name", "") or ""),
            )

    _push(image_url, image_base64, image_name)
    return out[:MAX_OUTREACH_PHOTOS]


def send_telegram_bundle(
    *,
    text: str,
    chat_id: str,
    via: str = "",
    attachments: list[dict[str, str]] | None = None,
    business_connection_id: Optional[str] = None,
    token: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Message first, then every attached photo — the tray order, appended.

    A caption tops out at 1024 chars and holds one picture, so a real sales
    draft with several cards cannot ride as one captioned photo. Send the text
    as text, then the photos after it.

    ``ok`` is False when ANY part fails. Reporting ok on a text-only delivery is
    exactly how attachments used to disappear without a word.
    """
    shots = list(attachments or [])
    if not shots:
        return send_telegram_message(
            text=text,
            chat_id=chat_id,
            via=via,
            business_connection_id=business_connection_id,
            token=token,
            timeout=timeout,
        )

    body = (text or "").strip()
    head: dict[str, Any] = {"ok": True}
    if body:
        head = send_telegram_message(
            text=body,
            chat_id=chat_id,
            via=via,
            business_connection_id=business_connection_id,
            token=token,
            timeout=timeout,
        )
        if not head.get("ok"):
            return head

    sent = 0
    failures: list[str] = []
    last: dict[str, Any] = {}
    for shot in shots:
        # Empty caption: the text already went out as its own message.
        res = send_telegram_message(
            text="",
            chat_id=chat_id,
            via=via,
            image_base64=shot.get("image_base64", ""),
            image_name=shot.get("image_name", "photo.jpg"),
            image_url=shot.get("image_url", ""),
            business_connection_id=business_connection_id,
            token=token,
            timeout=timeout,
        )
        last = res
        if res.get("ok"):
            sent += 1
        else:
            failures.append(str(res.get("detail") or res.get("error") or "ошибка"))

    out: dict[str, Any] = {
        "ok": sent == len(shots),
        "message_id": head.get("message_id") or last.get("message_id"),
        "chat_id": head.get("chat_id") or last.get("chat_id") or chat_id,
        # `via` is how the TEXT went; the photos may take another route
        # (personal MTProto), so name that separately instead of hiding it.
        "via": head.get("via") or last.get("via") or "",
        "photo_via": last.get("via") or "",
        "photos_sent": sent,
        "photos_total": len(shots),
    }
    if failures:
        out["error"] = "photo_send_failed"
        out["detail"] = (
            f"Текст ушёл, фото {sent}/{len(shots)}: " + "; ".join(failures[:3])
        )
    return out


def send_outreach_to_client(
    *,
    text: str,
    tg_nick: str = "",
    tg_conversation: str = "",
    tg_chat_id: str = "",
    via: str = "",
    image_base64: str = "",
    image_name: str = "photo.jpg",
    image_url: str = "",
    images: Any = None,
) -> dict[str, Any]:
    """Resolve client TG target and send text + the whole photo tray."""
    chat_id = resolve_telegram_chat_id(
        tg_nick=tg_nick,
        tg_conversation=tg_conversation,
        tg_chat_id=tg_chat_id,
    )
    return send_telegram_bundle(
        text=text,
        chat_id=chat_id,
        via=via,
        attachments=normalize_image_attachments(
            images, image_base64, image_name, image_url
        ),
    )