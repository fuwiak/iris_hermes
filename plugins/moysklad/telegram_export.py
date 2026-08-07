"""Import Telegram Desktop JSON export into MoySklad CRM threads.

Maps ``data/telegram_export.json`` (or ``$HERMES_HOME/moysklad/telegram_export.json``)
personal chats onto catalog clients by phone (via Contacts) and name, then:

* seeds local ``conversations`` history (TG conversation column / client card)
* fills empty ``ТГ ник`` when an ``@username`` can be attributed to the peer
* stores ``tg_chat_id`` for Bot API delivery

Export has no per-chat username field — nick fill is best-effort from mentions /
t.me links that clearly belong to the peer side.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Optional

from hermes_constants import get_hermes_home
from plugins.moysklad.conversations import (
    _MAX_MESSAGES,
    _LOCK,
    _ensure_thread,
    _load,
    _now,
    _save,
    normalize_tg_nick,
)
from plugins.moysklad.dedupe import normalize_name, normalize_phone as dedupe_phone

log = logging.getLogger(__name__)

_OVERLAY_NAME = "telegram_export_overlay.json"
_IMPORT_LOCK = threading.Lock()
_IMPORT_DONE_FOR: set[str] = set()

_TME_RE = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z0-9_]{4,64})",
    re.IGNORECASE,
)
_MENTION_RE = re.compile(r"@([A-Za-z0-9_]{4,64})")
_FROM_ID_RE = re.compile(r"^user(\d+)$", re.IGNORECASE)

# Mentions that are never the client peer (studio / bots / suppliers).
_SKIP_NICKS = frozenset({
    "veresk_flowers_msk",
    "get_myidrobot",
    "premiumrussia",
    "russian_seller",
    "cvetioptomru",
})


def default_export_paths() -> list[Path]:
    """Candidate paths for Telegram Desktop export JSON."""
    home = get_hermes_home() / "moysklad"
    cwd = Path.cwd()
    return [
        home / "telegram_export.json",
        cwd / "data" / "telegram_export.json",
        cwd / "telegram_export.json",
        Path(__file__).resolve().parents[2] / "data" / "telegram_export.json",
    ]


def resolve_export_path(explicit: str | Path | None = None) -> Optional[Path]:
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None
    env = ( __import__("os").environ.get("MOYSKLAD_TELEGRAM_EXPORT") or "").strip()
    if env:
        p = Path(env)
        if p.is_file():
            return p
    for p in default_export_paths():
        if p.is_file():
            return p
    return None


def _overlay_path() -> Path:
    root = get_hermes_home() / "moysklad"
    root.mkdir(parents=True, exist_ok=True)
    return root / _OVERLAY_NAME


def load_overlay() -> dict[str, Any]:
    path = _overlay_path()
    if not path.is_file():
        return {"by_client_id": {}, "stats": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"by_client_id": {}, "stats": {}}
    if not isinstance(raw, dict):
        return {"by_client_id": {}, "stats": {}}
    by_id = raw.get("by_client_id") if isinstance(raw.get("by_client_id"), dict) else {}
    return {"by_client_id": by_id, "stats": raw.get("stats") or {}}


def save_overlay(overlay: dict[str, Any]) -> None:
    path = _overlay_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(overlay, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def overlay_for_client(client_id: str) -> dict[str, Any]:
    cid = str(client_id or "").strip()
    if not cid:
        return {}
    entry = (load_overlay().get("by_client_id") or {}).get(cid)
    return dict(entry) if isinstance(entry, dict) else {}


def _message_text(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, list):
        parts: list[str] = []
        for item in raw:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
        return "".join(parts).strip()
    return str(raw).strip()


def _norm_phone_export(value: Any) -> str:
    """Export phones often look like ``0079…`` — fold into dedupe 10-digit key."""
    digits = re.sub(r"\D+", "", str(value or ""))
    if digits.startswith("00"):
        digits = digits[2:]
    return dedupe_phone(digits)


def _contact_phone_by_name(export: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    contacts = (export.get("contacts") or {}).get("list") or []
    if not isinstance(contacts, list):
        return out
    for c in contacts:
        if not isinstance(c, dict):
            continue
        name = normalize_name(
            " ".join(
                x.strip()
                for x in (c.get("first_name") or "", c.get("last_name") or "")
                if str(x or "").strip()
            )
        )
        phone = _norm_phone_export(c.get("phone_number"))
        if name and phone and name not in out:
            out[name] = phone
    return out


def _studio_user_id(export: dict[str, Any]) -> str:
    pi = export.get("personal_information") or {}
    return str(pi.get("user_id") or "").strip()


def _peer_user_id(chat: dict[str, Any], studio_id: str) -> str:
    """Infer peer Telegram user id from chat id / message from_id."""
    chat_id = str(chat.get("id") or "").strip()
    if chat_id.isdigit():
        return chat_id
    for msg in chat.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        m = _FROM_ID_RE.match(str(msg.get("from_id") or ""))
        if not m:
            continue
        uid = m.group(1)
        if uid and uid != studio_id:
            return uid
    return chat_id


def _extract_peer_nick(
    chat: dict[str, Any],
    *,
    studio_id: str,
    peer_id: str,
) -> str:
    """Best-effort @username for the peer (not the studio account)."""
    candidates: list[str] = []
    for msg in chat.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        direction_inbound = True
        m = _FROM_ID_RE.match(str(msg.get("from_id") or ""))
        if m and m.group(1) == studio_id:
            direction_inbound = False
        # Prefer mentions / links on inbound messages (client side).
        entities = list(msg.get("text_entities") or [])
        for ent in entities:
            if not isinstance(ent, dict):
                continue
            et = str(ent.get("type") or "")
            if et == "mention":
                nick = normalize_tg_nick(str(ent.get("text") or ""))
                if nick and nick not in _SKIP_NICKS:
                    if direction_inbound:
                        return nick
                    candidates.append(nick)
            if et in ("link", "text_link"):
                href = str(ent.get("href") or ent.get("text") or "")
                mm = _TME_RE.search(href)
                if mm:
                    nick = normalize_tg_nick(mm.group(1))
                    if nick and nick not in _SKIP_NICKS:
                        if direction_inbound:
                            return nick
                        candidates.append(nick)
        blob = _message_text(msg.get("text"))
        if direction_inbound:
            mm = _TME_RE.search(blob)
            if mm:
                nick = normalize_tg_nick(mm.group(1))
                if nick and nick not in _SKIP_NICKS:
                    return nick
    for nick in candidates:
        if nick not in _SKIP_NICKS:
            return nick
    return ""


def _chat_messages_for_store(
    chat: dict[str, Any],
    *,
    studio_id: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in chat.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        if str(msg.get("type") or "") != "message":
            continue
        text = _message_text(msg.get("text"))
        if not text:
            continue
        from_id = ""
        m = _FROM_ID_RE.match(str(msg.get("from_id") or ""))
        if m:
            from_id = m.group(1)
        direction = "outbound" if from_id and from_id == studio_id else "inbound"
        if from_id == studio_id:
            direction = "outbound"
        elif from_id:
            direction = "inbound"
        else:
            # Fallback: name match against studio first_name
            direction = "inbound"
        ts = str(msg.get("date") or "").strip()
        if ts and "T" not in ts:
            ts = ts.replace(" ", "T")
        if not ts:
            ts = _now()
        out.append({
            "id": f"tgexport-{msg.get('id') or len(out)}",
            "direction": direction,
            "channel": "telegram",
            "label": (
                "входящее · Telegram · export"
                if direction == "inbound"
                else "исходящее · Telegram · export"
            ),
            "text": text[:4000],
            "ts": ts,
            "source": "telegram_export",
        })
    if len(out) > _MAX_MESSAGES:
        out = out[-_MAX_MESSAGES:]
    return out


def _index_catalog_rows(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    by_phone: dict[str, dict[str, Any]] = {}
    by_name: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("_moysklad_id") or row.get("id") or "").strip()
        if not cid:
            continue
        phone = dedupe_phone(row.get("Телефон") or row.get("phone"))
        if phone and phone not in by_phone:
            by_phone[phone] = row
        name = normalize_name(row.get("Наименование") or row.get("name"))
        if name:
            by_name.setdefault(name, []).append(row)
    return by_phone, by_name


def _match_row(
    *,
    chat_name: str,
    phone: str,
    by_phone: dict[str, dict[str, Any]],
    by_name: dict[str, list[dict[str, Any]]],
) -> Optional[dict[str, Any]]:
    if phone and phone in by_phone:
        return by_phone[phone]
    name = normalize_name(chat_name)
    if not name:
        return None
    hits = by_name.get(name) or []
    if len(hits) == 1:
        return hits[0]
    # Prefix / containment soft match when unique.
    soft: list[dict[str, Any]] = []
    for key, rows in by_name.items():
        if name == key or name in key or key in name:
            soft.extend(rows)
    if len(soft) == 1:
        return soft[0]
    return None


def import_export_into_catalog(
    rows: list[dict[str, Any]],
    *,
    export_path: str | Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Match export chats → catalog rows; write conversations + overlay.

    Idempotent per ``HERMES_HOME`` fingerprint unless ``force=True``.
    """
    path = resolve_export_path(export_path)
    if path is None:
        return {"ok": False, "error": "export_not_found", "matched": 0, "imported_messages": 0}

    fp = f"{path}:{path.stat().st_mtime_ns}:{len(rows)}"
    with _IMPORT_LOCK:
        if not force and fp in _IMPORT_DONE_FOR:
            overlay = load_overlay()
            return {
                "ok": True,
                "skipped": True,
                "path": str(path),
                **(overlay.get("stats") or {}),
            }

        try:
            export = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {"ok": False, "error": str(exc), "matched": 0}

        studio_id = _studio_user_id(export)
        phone_by_name = _contact_phone_by_name(export)
        by_phone, by_name = _index_catalog_rows(rows)
        chats = (export.get("chats") or {}).get("list") or []
        if not isinstance(chats, list):
            chats = []

        overlay = load_overlay()
        by_client: dict[str, Any] = dict(overlay.get("by_client_id") or {})
        matched = 0
        imported_messages = 0
        nick_filled = 0

        with _LOCK:
            store = _load()
            for chat in chats:
                if not isinstance(chat, dict):
                    continue
                if str(chat.get("type") or "") != "personal_chat":
                    continue
                chat_name = str(chat.get("name") or "").strip()
                phone = phone_by_name.get(normalize_name(chat_name), "")
                row = _match_row(
                    chat_name=chat_name,
                    phone=phone,
                    by_phone=by_phone,
                    by_name=by_name,
                )
                if row is None:
                    continue
                client_id = str(row.get("_moysklad_id") or row.get("id") or "").strip()
                if not client_id:
                    continue
                matched += 1
                peer_id = _peer_user_id(chat, studio_id)
                nick = _extract_peer_nick(chat, studio_id=studio_id, peer_id=peer_id)
                client_phone = dedupe_phone(row.get("Телефон") or row.get("phone")) or phone
                existing_nick = normalize_tg_nick(
                    row.get("ТГ ник") or row.get("tg_nick") or ""
                )
                use_nick = existing_nick or nick
                if nick and not existing_nick:
                    nick_filled += 1
                    row["ТГ ник"] = f"@{nick}" if not nick.startswith("@") else nick
                    row["tg_nick"] = row["ТГ ник"]

                messages = _chat_messages_for_store(chat, studio_id=studio_id)
                thread = _ensure_thread(
                    store,
                    client_id=client_id,
                    phone=client_phone,
                    tg_nick=use_nick,
                    client_name=str(row.get("Наименование") or chat_name),
                )
                existing = list(thread.get("messages") or [])
                # Keep non-export messages; replace prior export batch.
                kept = [m for m in existing if str(m.get("source") or "") != "telegram_export"]
                merged = kept + messages
                if len(merged) > _MAX_MESSAGES:
                    merged = merged[-_MAX_MESSAGES:]
                merged.sort(key=lambda m: str(m.get("ts") or ""))
                thread["messages"] = merged
                thread["tg_chat_id"] = str(peer_id or "")
                thread["updated_at"] = _now()
                imported_messages += len(messages)

                by_client[client_id] = {
                    "tg_chat_id": str(peer_id or ""),
                    "tg_nick": f"@{use_nick}" if use_nick and not str(use_nick).startswith("@") else use_nick,
                    "phone": client_phone,
                    "chat_name": chat_name,
                    "message_count": len(messages),
                    "export_chat_id": str(chat.get("id") or ""),
                }

            _save(store)

        stats = {
            "ok": True,
            "path": str(path),
            "matched": matched,
            "imported_messages": imported_messages,
            "nick_filled": nick_filled,
            "chats_total": sum(
                1 for c in chats if isinstance(c, dict) and c.get("type") == "personal_chat"
            ),
        }
        save_overlay({"by_client_id": by_client, "stats": stats})
        _IMPORT_DONE_FOR.add(fp)
        log.info(
            "moysklad telegram export: matched=%s msgs=%s nicks=%s path=%s",
            matched,
            imported_messages,
            nick_filled,
            path,
        )
        return stats


def ensure_export_imported(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Lazy one-shot import when export file is present."""
    if resolve_export_path() is None:
        return {"ok": False, "skipped": True, "error": "export_not_found"}
    try:
        return import_export_into_catalog(rows)
    except Exception as exc:
        log.warning("moysklad telegram export import failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def apply_export_overlay_to_public(client: dict[str, Any]) -> dict[str, Any]:
    """Merge overlay tg_nick / tg_chat_id into a public client dict (empty slots only)."""
    if not isinstance(client, dict):
        return client
    cid = str(client.get("id") or "").strip()
    entry = overlay_for_client(cid)
    if not entry:
        return client
    out = dict(client)
    if not str(out.get("tg_nick") or "").strip() and entry.get("tg_nick"):
        out["tg_nick"] = entry["tg_nick"]
        fields = list(out.get("ai_fields") or [])
        if "tg_nick" not in fields:
            fields.append("tg_nick")
            out["ai_fields"] = fields
    if not str(out.get("tg_chat_id") or "").strip() and entry.get("tg_chat_id"):
        out["tg_chat_id"] = str(entry["tg_chat_id"])
    return out


def clear_import_memory_for_tests() -> None:
    _IMPORT_DONE_FOR.clear()
