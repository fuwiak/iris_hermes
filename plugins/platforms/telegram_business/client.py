"""Telegram Business Bot API client (shared by Office platform + MoySklad).

Env precedence (first non-empty wins):

* Token: ``TELEGRAM_BUSINESS_BOT_TOKEN`` → ``MOYSKLAD_TELEGRAM_BOT_TOKEN``
  → ``TELEGRAM_BOT_TOKEN``
* Connection id: ``TELEGRAM_BUSINESS_CONNECTION_ID`` →
  ``MOYSKLAD_TELEGRAM_BUSINESS_CONNECTION_ID`` → seller_settings
* Username: ``TELEGRAM_BUSINESS_BOT_USERNAME`` → ``MOYSKLAD_…`` → ``TELEGRAM_…``

Does not touch getUpdates / webhook — gateway Telegram owns inbound.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)

_API = "https://api.telegram.org"


def business_bot_token() -> str:
    return (
        (os.getenv("TELEGRAM_BUSINESS_BOT_TOKEN") or "").strip()
        or (os.getenv("MOYSKLAD_TELEGRAM_BOT_TOKEN") or "").strip()
        or (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    )


def business_bot_username() -> str:
    return (
        (os.getenv("TELEGRAM_BUSINESS_BOT_USERNAME") or "").strip().lstrip("@")
        or (os.getenv("MOYSKLAD_TELEGRAM_BOT_USERNAME") or "").strip().lstrip("@")
        or (os.getenv("TELEGRAM_BOT_USERNAME") or "").strip().lstrip("@")
    )


def resolve_business_connection_id(
    *,
    include_seller_settings: bool = True,
) -> str:
    env_id = (
        (os.getenv("TELEGRAM_BUSINESS_CONNECTION_ID") or "").strip()
        or (os.getenv("MOYSKLAD_TELEGRAM_BUSINESS_CONNECTION_ID") or "").strip()
    )
    if env_id:
        return env_id
    if not include_seller_settings:
        return ""
    try:
        from plugins.moysklad.campaigns import get_seller_settings

        stored = get_seller_settings().get("telegram_business_connection_id") or ""
        return str(stored).strip()
    except Exception:
        return ""


def telegram_api(
    method: str,
    *,
    token: str | None = None,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Call Bot API method. Returns parsed JSON ``{ok, ...}``; never raises for API errors."""
    token = (token or business_bot_token()).strip()
    method = (method or "").strip().lstrip("/")
    if not token:
        return {
            "ok": False,
            "error": "telegram_token_missing",
            "detail": (
                "Set TELEGRAM_BUSINESS_BOT_TOKEN (Office → Telegram Business) "
                "or MOYSKLAD_TELEGRAM_BOT_TOKEN / TELEGRAM_BOT_TOKEN"
            ),
        }
    if not method:
        return {"ok": False, "error": "telegram_method_missing", "detail": "method required"}

    url = f"{_API}/bot{token}/{method}"
    try:
        with httpx.Client(timeout=timeout) as client:
            if files:
                # Multipart upload (sendPhoto etc.): scalars go as form fields.
                form = {
                    k: v if isinstance(v, str) else json.dumps(v)
                    for k, v in ((json_body or params) or {}).items()
                    if v is not None
                }
                resp = client.post(url, data=form, files=files)
            elif json_body is not None:
                resp = client.post(url, json=json_body)
            else:
                resp = client.post(url, params=params or {})
            data = resp.json() if resp.content else {}
    except Exception as exc:  # pragma: no cover - network
        log.warning("telegram_business %s failed: %s", method, exc)
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
    biz = (business_connection_id or resolve_business_connection_id()).strip()
    if not biz:
        return {
            "ok": False,
            "error": "business_connection_missing",
            "detail": (
                "Set TELEGRAM_BUSINESS_CONNECTION_ID in Office → Telegram Business "
                "(or MOYSKLAD_TELEGRAM_BUSINESS_CONNECTION_ID)"
            ),
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


def telegram_send_status() -> dict[str, Any]:
    token = business_bot_token()
    return {
        "configured": bool(token),
        "bot_username": business_bot_username() or None,
        "business_connection_configured": bool(resolve_business_connection_id()),
        "business_connection_id": resolve_business_connection_id() or None,
    }


def telegram_account_snapshot(
    business_connection_id: str | None = None,
    *,
    token: str | None = None,
    probe: bool = True,
) -> dict[str, Any]:
    """UI-facing Telegram Business account block (no secrets)."""
    status = telegram_send_status()
    biz = (business_connection_id or resolve_business_connection_id() or "").strip()
    out: dict[str, Any] = {
        **status,
        "business_connection_id": biz or None,
        "account": None,
    }
    if not probe or not biz or not business_bot_token():
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


def probe_business_integration(
    *,
    token: str | None = None,
    business_connection_id: str | None = None,
) -> dict[str, Any]:
    """Office Test button — getMe + getBusinessConnection without gateway."""
    me = fetch_bot_identity(token=token)
    if not me.get("ok"):
        return {
            "ok": False,
            "message": me.get("detail") or me.get("error") or "getMe failed",
            "bot": me,
        }
    snap = telegram_account_snapshot(
        business_connection_id, token=token, probe=True
    )
    account = snap.get("account") or {}
    if not account.get("ok"):
        return {
            "ok": False,
            "message": account.get("detail")
            or account.get("error")
            or "Business connection missing or invalid",
            "bot": me,
            "telegram_account": snap,
        }
    nick = account.get("username") or "?"
    rights = []
    if account.get("can_reply"):
        rights.append("reply")
    if account.get("can_read_messages"):
        rights.append("read")
    rights_s = "+".join(rights) if rights else "no rights"
    return {
        "ok": True,
        "message": (
            f"Telegram Business OK — bot @{me.get('username')} → "
            f"@{nick} ({rights_s})"
        ),
        "bot": me,
        "telegram_account": snap,
    }
