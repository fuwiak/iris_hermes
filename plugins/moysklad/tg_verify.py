"""Persist Telegram peer reachability (nick still resolves) for CRM rows.

Separate overlay from ``telegram_export`` — verification is MTProto/Bot API
resolve, not Desktop export import. Used by:

* ``scripts/verify_telegram_peers.py`` — bulk check + cache write
* ``audience.require_telegram`` — only ``tg_active=True`` rows pass
* Clients table column «TG активен» between «ТГ ник» and «TG conversation»

Persistence: memory → Redis → ``$HERMES_HOME/moysklad/tg_verify_overlay.json``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional

from hermes_constants import get_hermes_home
from plugins.moysklad.conversations import normalize_tg_nick
from plugins.moysklad.dedupe import normalize_phone

log = logging.getLogger(__name__)

_OVERLAY_NAME = "tg_verify_overlay.json"
_REDIS_KEY = "moysklad:tg_verify:overlay:v1"
_CACHE_LOCK = threading.RLock()
_MEMORY: dict[str, Any] | None = None
_MEMORY_FP: str | None = None


def _account_fingerprint() -> str:
    token = (os.environ.get("MOYSKLAD_API_TOKEN") or "").strip()
    if not token:
        return "no-token"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _redis_url() -> str:
    return (os.environ.get("REDIS_URL") or os.environ.get("MOYSKLAD_REDIS_URL") or "").strip()


def _redis_client():
    url = _redis_url()
    if not url:
        return None
    try:
        import redis  # type: ignore[import-not-found]
    except Exception:
        return None
    try:
        client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2.0)
        client.ping()
        return client
    except Exception as exc:
        log.warning("MoySklad tg-verify Redis unavailable (%s); file cache", exc)
        return None


def _overlay_path() -> Path:
    root = get_hermes_home() / "moysklad"
    root.mkdir(parents=True, exist_ok=True)
    return root / _OVERLAY_NAME


def _redis_key() -> str:
    return f"{_REDIS_KEY}:{_account_fingerprint()}"


def _empty_overlay() -> dict[str, Any]:
    return {"by_client_id": {}, "stats": {}, "saved_at": 0.0}


def _normalize_overlay(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return _empty_overlay()
    by_id = raw.get("by_client_id") if isinstance(raw.get("by_client_id"), dict) else {}
    return {
        "by_client_id": by_id,
        "stats": raw.get("stats") if isinstance(raw.get("stats"), dict) else {},
        "saved_at": float(raw.get("saved_at") or 0),
    }


def _memory_fingerprint() -> str:
    return f"{_account_fingerprint()}:{_overlay_path()}"


def load_overlay() -> dict[str, Any]:
    global _MEMORY, _MEMORY_FP
    fp = _memory_fingerprint()
    with _CACHE_LOCK:
        if (
            _MEMORY_FP == fp
            and isinstance(_MEMORY, dict)
            and _MEMORY.get("by_client_id") is not None
        ):
            return {
                "by_client_id": dict(_MEMORY.get("by_client_id") or {}),
                "stats": dict(_MEMORY.get("stats") or {}),
                "saved_at": float(_MEMORY.get("saved_at") or 0),
            }

    client = _redis_client()
    if client is not None:
        try:
            raw = client.get(_redis_key())
            if raw:
                overlay = _normalize_overlay(json.loads(raw))
                with _CACHE_LOCK:
                    _MEMORY = overlay
                    _MEMORY_FP = fp
                return {
                    "by_client_id": dict(overlay.get("by_client_id") or {}),
                    "stats": dict(overlay.get("stats") or {}),
                    "saved_at": float(overlay.get("saved_at") or 0),
                }
        except Exception as exc:
            log.warning("MoySklad tg-verify Redis get failed: %s", exc)

    path = _overlay_path()
    if path.is_file():
        try:
            overlay = _normalize_overlay(json.loads(path.read_text(encoding="utf-8")))
            with _CACHE_LOCK:
                _MEMORY = overlay
                _MEMORY_FP = fp
            return {
                "by_client_id": dict(overlay.get("by_client_id") or {}),
                "stats": dict(overlay.get("stats") or {}),
                "saved_at": float(overlay.get("saved_at") or 0),
            }
        except (OSError, json.JSONDecodeError):
            pass
    return _empty_overlay()


def save_overlay(overlay: dict[str, Any]) -> None:
    global _MEMORY, _MEMORY_FP
    payload = _normalize_overlay(overlay)
    payload["saved_at"] = time.time()

    with _CACHE_LOCK:
        _MEMORY = payload
        _MEMORY_FP = _memory_fingerprint()

    client = _redis_client()
    if client is not None:
        try:
            client.setex(
                _redis_key(),
                30 * 24 * 60 * 60,
                json.dumps(payload, ensure_ascii=False, default=str),
            )
        except Exception as exc:
            log.warning("MoySklad tg-verify Redis set failed: %s", exc)

    path = _overlay_path()
    try:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except OSError as exc:
        log.warning("MoySklad tg-verify file write failed: %s", exc)


def overlay_for_client(client_id: str) -> dict[str, Any]:
    cid = str(client_id or "").strip()
    if not cid:
        return {}
    entry = (load_overlay().get("by_client_id") or {}).get(cid)
    return dict(entry) if isinstance(entry, dict) else {}


def tg_active_label(*, active: bool | None, has_contact: bool) -> str:
    if active is True:
        return "активен"
    if active is False:
        return "не найден"
    if has_contact:
        return "не проверен"
    return "—"


def row_tg_active(row: dict[str, Any]) -> bool | None:
    """Tri-state: True/False from overlay stamp; None = never checked."""
    if not isinstance(row, dict):
        return None
    if "tg_active" in row:
        val = row.get("tg_active")
        if val is True or val is False:
            return bool(val)
        if val is None:
            return None
    cid = str(row.get("_moysklad_id") or row.get("id") or "").strip()
    if not cid:
        return None
    entry = overlay_for_client(cid)
    if not entry or "active" not in entry:
        return None
    return bool(entry.get("active"))


def row_has_contact_for_tg_check(row: dict[str, Any]) -> bool:
    return bool(normalize_phone(row.get("Телефон") or row.get("phone")))


def row_passes_telegram_filter(row: dict[str, Any]) -> bool:
    """Audience chip «Telegram» — verified reachable via phone (or nick)."""
    active = row_tg_active(row)
    if active is True:
        return True
    if active is False:
        return False
    return False


def _stamp_row_from_entry(row: dict[str, Any], entry: dict[str, Any]) -> bool:
    if "active" not in entry:
        return False
    active = bool(entry.get("active"))
    resolved = normalize_tg_nick(entry.get("resolved_nick") or "")
    detail = str(entry.get("detail") or "").strip()
    label = tg_active_label(active=active, has_contact=row_has_contact_for_tg_check(row))
    changed = False
    if row.get("tg_active") is not active:
        row["tg_active"] = active
        changed = True
    if row.get("tg_active_label") != label:
        row["tg_active_label"] = label
        changed = True
    if resolved and row.get("tg_active_nick") != resolved:
        row["tg_active_nick"] = resolved
        changed = True
    if detail and row.get("tg_active_detail") != detail:
        row["tg_active_detail"] = detail
        changed = True
    checked_at = entry.get("checked_at")
    if checked_at is not None and row.get("tg_active_checked_at") != checked_at:
        row["tg_active_checked_at"] = checked_at
        changed = True
    return changed


def stamp_catalog_rows_from_verify(rows: list[dict[str, Any]]) -> int:
    """Apply overlay verification onto catalog rows (mutates)."""
    by_id = load_overlay().get("by_client_id") or {}
    if not by_id:
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row_has_contact_for_tg_check(row) and "tg_active_label" not in row:
                row["tg_active_label"] = tg_active_label(
                    active=None, has_contact=True
                )
        return 0
    stamped = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("_moysklad_id") or row.get("id") or "").strip()
        entry = by_id.get(cid) if cid else None
        if isinstance(entry, dict) and _stamp_row_from_entry(row, entry):
            stamped += 1
        elif row_has_contact_for_tg_check(row) and "tg_active_label" not in row:
            row["tg_active_label"] = tg_active_label(active=None, has_contact=True)
    return stamped


def save_verify_results_bulk(results: dict[str, dict[str, Any]]) -> int:
    """Upsert many client verifications in one overlay write."""
    if not results:
        return 0
    overlay = load_overlay()
    by_id = dict(overlay.get("by_client_id") or {})
    now = time.time()
    written = 0
    for cid, result in results.items():
        key = str(cid or "").strip()
        if not key:
            continue
        by_id[key] = {
            "active": bool(result.get("active")),
            "checked_at": float(result.get("checked_at") or now),
            "resolved_nick": normalize_tg_nick(result.get("resolved_nick") or ""),
            "chat_id": str(result.get("chat_id") or "").strip(),
            "via": str(result.get("via") or "").strip(),
            "detail": str(result.get("detail") or "").strip(),
        }
        written += 1
    stats = dict(overlay.get("stats") or {})
    stats["last_run_at"] = now
    stats["total_checked"] = len(by_id)
    stats["active"] = sum(1 for v in by_id.values() if isinstance(v, dict) and v.get("active"))
    stats["inactive"] = sum(
        1 for v in by_id.values() if isinstance(v, dict) and v.get("active") is False
    )
    overlay["by_client_id"] = by_id
    overlay["stats"] = stats
    save_overlay(overlay)
    return written


def match_catalog_phones_to_contacts(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Mark ``tg_active`` from personal Telegram contacts cache (no live MTProto).

    Matches catalog «Телефон» to ``contacts.json`` phone — this is the fast
    path after «Синхр.» in Рассылки.
    """
    from plugins.platforms.telegram_user.client import cached_contacts, phone_lookup_key

    index: dict[str, dict[str, Any]] = {}
    for contact in cached_contacts():
        if not isinstance(contact, dict):
            continue
        key = phone_lookup_key(str(contact.get("phone") or ""))
        if key and key not in index:
            index[key] = contact
    hits: dict[str, dict[str, Any]] = {}
    scanned = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        phone = str(row.get("Телефон") or row.get("phone") or "")
        key = phone_lookup_key(phone)
        if not key:
            continue
        scanned += 1
        contact = index.get(key)
        if not contact:
            continue
        cid = str(row.get("_moysklad_id") or row.get("id") or "").strip()
        if not cid:
            continue
        hits[cid] = {
            "active": True,
            "resolved_nick": contact.get("tg_nick") or "",
            "chat_id": str(contact.get("tg_chat_id") or contact.get("id") or ""),
            "via": "contacts_cache",
            "detail": "",
        }
    written = save_verify_results_bulk(hits)
    return {"scanned": scanned, "matched": written, "contacts_with_phone": len(index)}


def save_verify_result(client_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """Upsert one client verification into the overlay."""
    cid = str(client_id or "").strip()
    if not cid:
        return {}
    overlay = load_overlay()
    by_id = dict(overlay.get("by_client_id") or {})
    entry = {
        "active": bool(result.get("active")),
        "checked_at": float(result.get("checked_at") or time.time()),
        "resolved_nick": normalize_tg_nick(result.get("resolved_nick") or ""),
        "chat_id": str(result.get("chat_id") or "").strip(),
        "via": str(result.get("via") or "").strip(),
        "detail": str(result.get("detail") or "").strip(),
    }
    by_id[cid] = entry
    stats = dict(overlay.get("stats") or {})
    stats["last_run_at"] = time.time()
    stats["total_checked"] = len(by_id)
    stats["active"] = sum(1 for v in by_id.values() if isinstance(v, dict) and v.get("active"))
    stats["inactive"] = sum(
        1 for v in by_id.values() if isinstance(v, dict) and v.get("active") is False
    )
    overlay["by_client_id"] = by_id
    overlay["stats"] = stats
    save_overlay(overlay)
    return entry


def verify_client_peers(
    *,
    client_id: str = "",
    phone: str = "",
    tg_nick: str = "",
    tg_chat_id: str = "",
    tg_conversation: str = "",
) -> dict[str, Any]:
    """Resolve phone first (колонка Телефон), then chat id / @nick."""
    from plugins.moysklad.telegram_send import preflight_recipient
    from plugins.platforms.telegram_user import client as tg_user

    nick = normalize_tg_nick(tg_nick)
    chat_id = str(tg_chat_id or "").strip()
    phone_raw = str(phone or "").strip()
    phone_digits = re.sub(r"\D+", "", phone_raw)

    last_error: dict[str, Any] = {}
    try:
        if phone_raw:
            res = tg_user.resolve_peer(
                tg_user.normalize_login_phone(phone_raw) or phone_raw
            )
            if res.get("ok") and (res.get("tg_chat_id") or res.get("id")):
                resolved = normalize_tg_nick(res.get("tg_nick") or nick)
                return {
                    "ok": True,
                    "active": True,
                    "checked": True,
                    "chat_id": str(res.get("tg_chat_id") or res.get("id")),
                    "resolved_nick": resolved,
                    "via": str(res.get("resolved_via") or "phone"),
                    "detail": "",
                }
            if res.get("error") == "phone_not_on_telegram":
                return {
                    "ok": True,
                    "active": False,
                    "checked": True,
                    "resolved_nick": nick,
                    "detail": str(res.get("detail") or "На этом номере нет Telegram"),
                    "error": "phone_not_on_telegram",
                }
            last_error = res
        if tg_user.is_authorized():
            peers = [
                p
                for p in (
                    chat_id,
                    f"@{nick.lstrip('@')}" if nick else "",
                )
                if p
            ]
            for peer in peers:
                res = tg_user.resolve_peer(peer)
                if res.get("ok") and (res.get("tg_chat_id") or res.get("id")):
                    resolved = normalize_tg_nick(res.get("tg_nick") or nick)
                    return {
                        "ok": True,
                        "active": True,
                        "checked": True,
                        "chat_id": str(res.get("tg_chat_id") or res.get("id")),
                        "resolved_nick": resolved,
                        "via": str(res.get("resolved_via") or "mtproto"),
                        "detail": "",
                    }
                last_error = res
    except Exception as exc:
        last_error = {"error": "telegram_user_error", "detail": str(exc)}

    if last_error.get("error") in {
        "not_authorized",
        "network_unreachable",
        "gateway_unreachable",
        "timeout",
        "gateway_missing",
    }:
        return {
            "ok": True,
            "active": False,
            "checked": False,
            "detail": str(
                last_error.get("detail")
                or "Личный Telegram не подключён — телефон не проверен."
            ),
            "error": last_error.get("error"),
        }

    if not nick and not chat_id and not phone_digits:
        return {
            "ok": True,
            "active": False,
            "checked": False,
            "detail": "Нет телефона, ТГ ника и chat id — проверить нечего.",
        }

    check = preflight_recipient(
        tg_nick=tg_nick,
        tg_conversation=tg_conversation,
        tg_chat_id=tg_chat_id,
    )
    if check.get("ok"):
        resolved = normalize_tg_nick(check.get("tg_nick") or nick)
        return {
            "ok": True,
            "active": True,
            "checked": True,
            "chat_id": str(check.get("chat_id") or ""),
            "resolved_nick": resolved,
            "via": str(check.get("resolved_via") or "business_bot"),
            "detail": "",
        }

    # Bot API «chat not found» is not proof the phone has no Telegram.
    return {
        "ok": True,
        "active": False,
        "checked": False,
        "resolved_nick": nick,
        "detail": str(
            last_error.get("detail")
            or check.get("detail")
            or "Не удалось проверить телефон (нужен личный Telegram)."
        ),
        "error": last_error.get("error") or check.get("error"),
    }


def verify_catalog_row(row: dict[str, Any]) -> dict[str, Any]:
    """Verify one deduped catalog row; persists when ``id`` is present."""
    cid = str(row.get("_moysklad_id") or row.get("id") or "").strip()
    result = verify_client_peers(
        client_id=cid,
        phone=str(row.get("Телефон") or row.get("phone") or ""),
        tg_nick=str(row.get("ТГ ник") or row.get("tg_nick") or ""),
        tg_chat_id=str(row.get("tg_chat_id") or ""),
        tg_conversation=str(row.get("TG conversation") or row.get("tg_conversation") or ""),
    )
    if cid and result.get("checked"):
        save_verify_result(cid, result)
    return result
