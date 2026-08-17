"""ТГ архив — every personal chat from the Telegram Desktop export.

``telegram_export.py`` only keeps chats it could attach to a MoySklad client.
Everything it failed to match used to vanish, so the operator saw «нет
переписок» for most of the archive. This module keeps the **full** picture:

* an index entry per personal chat (matched or not) — name, @nick, phone,
  numeric peer id, message count, last message, matched client id
* a readable thread for every chat, stored in the same ``conversations`` store
  (``client_id`` for matched chats, ``tg:<peer id>`` for unmatched ones)

The numeric peer id is what Telegram Business ``sendMessage`` needs — Bot API
cannot resolve a cold ``@username``. Indexing the whole archive therefore also
makes those peers reachable from Рассылки.

Persistence ladder (same as catalog / overlay / conversations):

1. Redis — when ``REDIS_URL`` / ``MOYSKLAD_REDIS_URL`` is set
2. File ``$HERMES_HOME/moysklad/telegram_archive.json``
3. Process-local memory

Only the index is persisted here (small); message bodies live in the
conversations store, which already caps and rotates them.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional

from hermes_constants import get_hermes_home
from plugins.moysklad.conversations import (
    _LOCK as _CONV_LOCK,
    _MAX_MESSAGES,
    _ensure_thread,
    _load as _conv_load,
    _now,
    _save as _conv_save,
    normalize_tg_nick,
    preview_text,
    public_thread,
)
from plugins.moysklad.dedupe import phone_query_matches
from plugins.moysklad.telegram_export import (
    _account_fingerprint,
    _chat_messages_for_store,
    _contact_phone_by_name,
    _extract_peer_nick,
    _peer_user_id,
    _phones_in_messages,
    _redis_client,
    _studio_user_id,
    cache_backend_name,
    fold_name,
    import_export_into_catalog,
    load_overlay,
    resolve_export_path,
)

log = logging.getLogger(__name__)

_INDEX_NAME = "telegram_archive.json"
_INDEX_REDIS_KEY = "moysklad:telegram_archive:index:v1"
_LOCK = threading.RLock()
_MEMORY: dict[str, Any] | None = None
_MEMORY_FP: str | None = None

# Unmatched chats live under this synthetic client id in the conversations store.
ARCHIVE_ID_PREFIX = "tg:"

DEFAULT_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days

# Unmatched chats are for reading context, not for AI grounding — keep them short.
_ARCHIVE_MAX_MESSAGES = 80

# Telegram's own service account — never a client, and its login codes have no
# business sitting in a CRM archive.
_SERVICE_PEER_IDS = frozenset({"777000"})


def archive_client_id(peer_id: str) -> str:
    return f"{ARCHIVE_ID_PREFIX}{str(peer_id or '').strip()}"


def cache_ttl_seconds() -> int:
    raw = (os.environ.get("MOYSKLAD_TG_ARCHIVE_TTL_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_TTL_SECONDS
    try:
        return max(3600, int(raw))
    except ValueError:
        return DEFAULT_TTL_SECONDS


def _index_path() -> Path:
    root = get_hermes_home() / "moysklad"
    root.mkdir(parents=True, exist_ok=True)
    return root / _INDEX_NAME


def _redis_key() -> str:
    return f"{_INDEX_REDIS_KEY}:{_account_fingerprint()}"


def _memory_fingerprint() -> str:
    return f"{_account_fingerprint()}:{_index_path()}"


def _empty_index() -> dict[str, Any]:
    return {"chats": {}, "stats": {}, "saved_at": 0.0}


def _normalize_index(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return _empty_index()
    chats = raw.get("chats") if isinstance(raw.get("chats"), dict) else {}
    return {
        "chats": chats,
        "stats": raw.get("stats") if isinstance(raw.get("stats"), dict) else {},
        "saved_at": float(raw.get("saved_at") or 0),
        "cache_backend": str(raw.get("cache_backend") or ""),
    }


def load_index() -> dict[str, Any]:
    """Load the archive index: memory → Redis → file."""
    global _MEMORY, _MEMORY_FP
    fp = _memory_fingerprint()
    with _LOCK:
        if _MEMORY_FP == fp and isinstance(_MEMORY, dict):
            return dict(_MEMORY)

    client = _redis_client()
    if client is not None:
        try:
            raw = client.get(_redis_key())
            if raw:
                index = _normalize_index(json.loads(raw))
                index["cache_backend"] = "redis"
                with _LOCK:
                    _MEMORY = index
                    _MEMORY_FP = fp
                return dict(index)
        except Exception as exc:
            log.warning("MoySklad tg-archive Redis get failed: %s", exc)

    path = _index_path()
    if path.is_file():
        try:
            index = _normalize_index(json.loads(path.read_text(encoding="utf-8")))
            index["cache_backend"] = "file"
            with _LOCK:
                _MEMORY = index
                _MEMORY_FP = fp
            return dict(index)
        except (OSError, json.JSONDecodeError):
            pass
    return _empty_index()


def save_index(index: dict[str, Any]) -> None:
    """Persist index to memory + Redis + file."""
    global _MEMORY, _MEMORY_FP
    payload = _normalize_index(index)
    payload["saved_at"] = time.time()
    payload["cache_backend"] = cache_backend_name()

    with _LOCK:
        _MEMORY = payload
        _MEMORY_FP = _memory_fingerprint()

    client = _redis_client()
    if client is not None:
        try:
            client.setex(
                _redis_key(),
                cache_ttl_seconds(),
                json.dumps(payload, ensure_ascii=False, default=str),
            )
        except Exception as exc:
            log.warning("MoySklad tg-archive Redis set failed: %s", exc)

    path = _index_path()
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError as exc:
        log.warning("MoySklad tg-archive file write failed: %s", exc)


def clear_memory_for_tests() -> None:
    global _MEMORY, _MEMORY_FP
    with _LOCK:
        _MEMORY = None
        _MEMORY_FP = None


def _entry_from_chat(
    chat: dict[str, Any],
    *,
    studio_id: str,
    phone_by_name: dict[str, str],
) -> dict[str, Any]:
    chat_name = str(chat.get("name") or "").strip()
    peer_id = _peer_user_id(chat, studio_id)
    nick = _extract_peer_nick(chat, studio_id=studio_id, peer_id=peer_id)
    messages = _chat_messages_for_store(chat, studio_id=studio_id)
    phone = phone_by_name.get(fold_name(chat_name), "")
    text_phones = _phones_in_messages(messages)
    inbound = sum(1 for m in messages if m.get("direction") == "inbound")
    return {
        "chat_id": str(peer_id or ""),
        "export_chat_id": str(chat.get("id") or ""),
        "name": chat_name,
        "tg_nick": nick,
        "phone": phone,
        "phones_in_text": text_phones,
        "message_count": len(messages),
        "inbound_count": inbound,
        "first_ts": str(messages[0].get("ts") or "") if messages else "",
        "last_ts": str(messages[-1].get("ts") or "") if messages else "",
        "_messages": messages,
    }


def rebuild(
    rows: list[dict[str, Any]] | None = None,
    *,
    export_path: str | Path | None = None,
    force: bool = True,
) -> dict[str, Any]:
    """Parse the export once; index every personal chat, matched or not.

    ``rows`` are catalog rows — when given, the matched half is delegated to
    ``telegram_export.import_export_into_catalog`` (unchanged behaviour) and
    this pass only adds what it could not attach.
    """
    path = resolve_export_path(export_path)
    if path is None:
        index = load_index()
        return {
            "ok": bool(index.get("chats")),
            "error": None if index.get("chats") else "export_not_found",
            "chats_total": len(index.get("chats") or {}),
            "cached": True,
            "cache_backend": cache_backend_name(),
            **(index.get("stats") or {}),
        }

    try:
        export = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": str(exc), "chats_total": 0}

    import_stats: dict[str, Any] = {}
    if rows:
        import_stats = import_export_into_catalog(
            rows, export_path=path, force=force, export_data=export
        )

    overlay = load_overlay()
    by_client = overlay.get("by_client_id") or {}
    client_by_chat: dict[str, str] = {}
    name_by_client: dict[str, str] = {}
    for cid, entry in by_client.items():
        if not isinstance(entry, dict):
            continue
        chat_id = str(entry.get("tg_chat_id") or "").strip()
        if chat_id:
            client_by_chat[chat_id] = str(cid)
        name_by_client[str(cid)] = str(entry.get("chat_name") or "")

    row_name_by_id: dict[str, str] = {}
    for row in rows or []:
        if isinstance(row, dict):
            rid = str(row.get("_moysklad_id") or row.get("id") or "").strip()
            if rid:
                row_name_by_id[rid] = str(row.get("Наименование") or row.get("name") or "")

    studio_id = _studio_user_id(export)
    phone_by_name = _contact_phone_by_name(export)
    chats = (export.get("chats") or {}).get("list") or []
    if not isinstance(chats, list):
        chats = []

    index_chats: dict[str, Any] = {}
    matched = 0
    unmatched = 0
    archived_messages = 0

    with _CONV_LOCK:
        store = _conv_load()
        for chat in chats:
            if not isinstance(chat, dict):
                continue
            if str(chat.get("type") or "") != "personal_chat":
                continue
            entry = _entry_from_chat(
                chat, studio_id=studio_id, phone_by_name=phone_by_name
            )
            chat_id = entry["chat_id"]
            if not chat_id or chat_id in _SERVICE_PEER_IDS:
                continue
            messages = entry.pop("_messages")
            client_id = client_by_chat.get(chat_id, "")
            entry["client_id"] = client_id
            entry["client_name"] = (
                row_name_by_id.get(client_id) or name_by_client.get(client_id) or ""
            )
            entry["matched"] = bool(client_id)

            if client_id:
                matched += 1
                # Thread already written by import_export_into_catalog.
                thread_id = client_id
            else:
                unmatched += 1
                thread_id = archive_client_id(chat_id)
                trimmed = messages[-_ARCHIVE_MAX_MESSAGES:]
                thread = _ensure_thread(
                    store,
                    client_id=thread_id,
                    phone=entry.get("phone") or "",
                    tg_nick=entry.get("tg_nick") or "",
                    client_name=entry.get("name") or "",
                )
                kept = [
                    m
                    for m in list(thread.get("messages") or [])
                    if str(m.get("source") or "") != "telegram_export"
                ]
                merged = kept + trimmed
                if len(merged) > _MAX_MESSAGES:
                    merged = merged[-_MAX_MESSAGES:]
                merged.sort(key=lambda m: str(m.get("ts") or ""))
                thread["messages"] = merged
                thread["tg_chat_id"] = chat_id
                thread["updated_at"] = _now()
                archived_messages += len(trimmed)
                entry["preview"] = preview_text(thread)

            entry["thread_id"] = thread_id
            if not entry.get("preview"):
                entry["preview"] = (
                    str((by_client.get(client_id) or {}).get("preview") or "")
                    if client_id
                    else ""
                )
            index_chats[chat_id] = entry
        _conv_save(store)

    stats = {
        "ok": True,
        "path": str(path),
        "chats_total": len(index_chats),
        "matched": matched,
        "unmatched": unmatched,
        "archived_messages": archived_messages,
        "cache_backend": cache_backend_name(),
        "rebuilt_at": _now(),
    }
    if import_stats:
        stats["import"] = {
            k: v for k, v in import_stats.items() if k not in ("path",)
        }
    save_index({"chats": index_chats, "stats": stats})
    log.info(
        "moysklad tg archive: chats=%s matched=%s unmatched=%s backend=%s",
        len(index_chats),
        matched,
        unmatched,
        cache_backend_name(),
    )
    return stats


def ensure_index(rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Build the index once per export file; cheap no-op afterwards."""
    index = load_index()
    path = resolve_export_path()
    if index.get("chats") and path is None:
        return {"ok": True, "cached": True, **(index.get("stats") or {})}
    if index.get("chats"):
        stats = index.get("stats") or {}
        if str(stats.get("path") or "") == str(path):
            return {"ok": True, "cached": True, **stats}
    return rebuild(rows, force=True)


def _public_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "chat_id": entry.get("chat_id") or "",
        "name": entry.get("name") or "",
        "tg_nick": entry.get("tg_nick") or "",
        "phone": entry.get("phone") or "",
        "message_count": int(entry.get("message_count") or 0),
        "inbound_count": int(entry.get("inbound_count") or 0),
        "first_ts": entry.get("first_ts") or "",
        "last_ts": entry.get("last_ts") or "",
        "preview": entry.get("preview") or "",
        "client_id": entry.get("client_id") or "",
        "client_name": entry.get("client_name") or "",
        "matched": bool(entry.get("matched")),
        "thread_id": entry.get("thread_id") or "",
    }


def list_chats(
    *,
    q: str = "",
    state: str = "all",
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Browse the archive. ``state``: all | matched | unmatched."""
    index = load_index()
    entries = [
        e for e in (index.get("chats") or {}).values() if isinstance(e, dict)
    ]
    state = (state or "all").strip().lower()
    if state == "matched":
        entries = [e for e in entries if e.get("matched")]
    elif state == "unmatched":
        entries = [e for e in entries if not e.get("matched")]

    needle = (q or "").strip().lower().lstrip("@")
    if needle:
        folded = fold_name(needle)
        entries = [
            e
            for e in entries
            if needle in str(e.get("name") or "").lower()
            or folded in fold_name(str(e.get("name") or ""))
            or needle in str(e.get("tg_nick") or "").lower()
            or phone_query_matches(e.get("phone") or "", needle)
            or needle in str(e.get("chat_id") or "")
            or needle in str(e.get("client_name") or "").lower()
            or needle in str(e.get("preview") or "").lower()
        ]

    entries.sort(key=lambda e: str(e.get("last_ts") or ""), reverse=True)
    total = len(entries)
    off = max(0, int(offset or 0))
    lim = max(1, min(int(limit or 100), 500))
    page = entries[off : off + lim]
    all_entries = [e for e in (index.get("chats") or {}).values() if isinstance(e, dict)]
    return {
        "ok": True,
        "chats": [_public_entry(e) for e in page],
        "matched_total": total,
        "returned": len(page),
        "offset": off,
        "limit": lim,
        "has_more": off + len(page) < total,
        "next_offset": off + len(page) if off + len(page) < total else None,
        "counts": {
            "total": len(all_entries),
            "matched": sum(1 for e in all_entries if e.get("matched")),
            "unmatched": sum(1 for e in all_entries if not e.get("matched")),
        },
        "stats": index.get("stats") or {},
        "cache_backend": index.get("cache_backend") or cache_backend_name(),
    }


def get_chat(chat_id: str) -> dict[str, Any]:
    """One archive chat + its full stored thread."""
    key = str(chat_id or "").strip()
    if key.startswith(ARCHIVE_ID_PREFIX):
        key = key[len(ARCHIVE_ID_PREFIX) :]
    entry = (load_index().get("chats") or {}).get(key)
    if not isinstance(entry, dict):
        return {"ok": False, "error": "chat_not_found", "chat_id": key}
    thread_id = str(entry.get("thread_id") or "") or archive_client_id(key)
    with _CONV_LOCK:
        store = _conv_load()
        thread = (store.get("threads") or {}).get(thread_id)
    return {
        "ok": True,
        "chat": _public_entry(entry),
        "conversation": public_thread(thread if isinstance(thread, dict) else None),
    }


def _peer_shape(entry: dict[str, Any]) -> dict[str, Any]:
    """Peer in the shape ``telegram_send`` / contact stores expect."""
    public = _public_entry(entry)
    return {
        **public,
        "tg_chat_id": public["chat_id"],
        "tg_nick": normalize_tg_nick(public["tg_nick"]),
    }


def find_peer(*, tg_nick: str = "", tg_chat_id: str = "") -> Optional[dict[str, Any]]:
    """Resolve a peer from the archive — gives Business send its numeric id."""
    nick = normalize_tg_nick(tg_nick)
    chat_id = str(tg_chat_id or "").strip()
    if not nick and not chat_id:
        return None
    chats = load_index().get("chats") or {}
    if chat_id:
        entry = chats.get(chat_id)
        if isinstance(entry, dict):
            return _peer_shape(entry)
    if nick:
        for entry in chats.values():
            if not isinstance(entry, dict):
                continue
            if normalize_tg_nick(entry.get("tg_nick") or "") == nick:
                return _peer_shape(entry)
    return None


def archive_contacts(*, unmatched_only: bool = True) -> list[dict[str, Any]]:
    """Archive peers as outreach contacts (``tg:<peer id>``, numeric-ready)."""
    out: list[dict[str, Any]] = []
    for entry in (load_index().get("chats") or {}).values():
        if not isinstance(entry, dict):
            continue
        if unmatched_only and entry.get("matched"):
            continue
        chat_id = str(entry.get("chat_id") or "").strip()
        if not chat_id:
            continue
        nick = normalize_tg_nick(entry.get("tg_nick") or "")
        name = str(entry.get("name") or "").strip()
        label_bits = [name] if name else []
        if nick:
            label_bits.append(f"@{nick}")
        else:
            label_bits.append(chat_id)
        out.append({
            "id": f"{ARCHIVE_ID_PREFIX}{chat_id}",
            "name": name or (f"@{nick}" if nick else chat_id),
            "tg_nick": nick,
            "tg_chat_id": chat_id,
            "source": "tg_archive",
            "label": " · ".join(label_bits),
        })
    return out
