"""Per-client messaging thread store for MoySklad CRM.

MVP: local append-on-send under ``$HERMES_HOME/moysklad/conversations.json``.
Linked by client_id, normalized phone, and Telegram nick. Full live pull from
gateway Telegram sessions can attach later via the same keys (see README).
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from hermes_constants import get_hermes_home

_LOCK = threading.Lock()
_PHONE_RE = re.compile(r"\D+")
_URL_RE = re.compile(r"^https?://|^tg:", re.IGNORECASE)

# Soft cap so the JSON store stays readable in UI / AI facts.
_MAX_MESSAGES = 200


def _store_path() -> Path:
    root = get_hermes_home() / "moysklad"
    root.mkdir(parents=True, exist_ok=True)
    return root / "conversations.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_phone(phone: str) -> str:
    digits = _PHONE_RE.sub("", phone or "")
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    return digits


def normalize_tg_nick(nick: str) -> str:
    n = (nick or "").strip()
    if n.startswith("@"):
        n = n[1:]
    return n.lower()


def _empty_store() -> dict[str, Any]:
    return {"threads": {}, "index": {}}


def _load() -> dict[str, Any]:
    path = _store_path()
    if not path.is_file():
        return _empty_store()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_store()
    if not isinstance(raw, dict):
        return _empty_store()
    threads = raw.get("threads") if isinstance(raw.get("threads"), dict) else {}
    index = raw.get("index") if isinstance(raw.get("index"), dict) else {}
    return {"threads": threads, "index": index}


def _save(store: dict[str, Any]) -> None:
    path = _store_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(store, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _index_keys(
    *,
    client_id: str = "",
    phone: str = "",
    tg_nick: str = "",
) -> list[str]:
    keys: list[str] = []
    cid = (client_id or "").strip()
    if cid:
        keys.append(f"id:{cid}")
    digits = normalize_phone(phone)
    if digits:
        keys.append(f"phone:{digits}")
    nick = normalize_tg_nick(tg_nick)
    if nick:
        keys.append(f"tg:{nick}")
    return keys


def _resolve_thread_id(store: dict[str, Any], keys: list[str]) -> Optional[str]:
    index = store.get("index") or {}
    for key in keys:
        tid = index.get(key)
        if tid and tid in (store.get("threads") or {}):
            return str(tid)
    # Fall back: client_id key is also the thread id when present.
    for key in keys:
        if key.startswith("id:"):
            cid = key[3:]
            if cid in (store.get("threads") or {}):
                return cid
    return None


def _ensure_thread(
    store: dict[str, Any],
    *,
    client_id: str,
    phone: str = "",
    tg_nick: str = "",
    client_name: str = "",
) -> dict[str, Any]:
    keys = _index_keys(client_id=client_id, phone=phone, tg_nick=tg_nick)
    tid = _resolve_thread_id(store, keys) or (client_id or "").strip() or str(uuid.uuid4())
    threads = store.setdefault("threads", {})
    index = store.setdefault("index", {})
    thread = threads.get(tid)
    if not isinstance(thread, dict):
        thread = {
            "client_id": (client_id or "").strip() or tid,
            "client_name": (client_name or "").strip(),
            "phone": normalize_phone(phone) or "",
            "tg_nick": normalize_tg_nick(tg_nick) or "",
            "messages": [],
            "created_at": _now(),
            "updated_at": _now(),
        }
        threads[tid] = thread
    else:
        if client_name and not thread.get("client_name"):
            thread["client_name"] = client_name.strip()
        if phone and not thread.get("phone"):
            thread["phone"] = normalize_phone(phone)
        if tg_nick and not thread.get("tg_nick"):
            thread["tg_nick"] = normalize_tg_nick(tg_nick)
    for key in keys:
        index[key] = tid
    return thread


def _channel_label(channel: str, direction: str) -> str:
    ch = (channel or "telegram").strip().lower()
    if ch == "whatsapp":
        ch_label = "WhatsApp"
    elif ch == "telegram_channel":
        ch_label = "Telegram-канал"
    else:
        ch_label = "Telegram"
    if direction == "inbound":
        return f"входящее · {ch_label}"
    if direction == "system":
        return f"система · {ch_label}"
    return f"исходящее · {ch_label}"


def preview_text(thread: dict[str, Any] | None, *, max_chars: int = 96) -> str:
    """One-line preview for the Clients table column."""
    if not thread:
        return ""
    messages = list(thread.get("messages") or [])
    if not messages:
        return ""
    last = messages[-1]
    label = str(last.get("label") or "").strip()
    text = " ".join(str(last.get("text") or "").split())
    if not text:
        return label or ""
    head = f"[{label}] " if label else ""
    blob = head + text
    if len(blob) <= max_chars:
        return blob
    return blob[: max_chars - 1].rstrip() + "…"


def public_thread(thread: dict[str, Any] | None) -> dict[str, Any]:
    if not thread:
        return {
            "client_id": "",
            "messages": [],
            "message_count": 0,
            "preview": "",
            "updated_at": None,
            "empty": True,
        }
    messages = list(thread.get("messages") or [])
    return {
        "client_id": thread.get("client_id") or "",
        "client_name": thread.get("client_name") or "",
        "phone": thread.get("phone") or "",
        "tg_nick": thread.get("tg_nick") or "",
        "tg_chat_id": thread.get("tg_chat_id") or "",
        "messages": messages,
        "message_count": len(messages),
        "preview": preview_text(thread),
        "updated_at": thread.get("updated_at"),
        "empty": not messages,
    }


def get_thread(
    *,
    client_id: str = "",
    phone: str = "",
    tg_nick: str = "",
) -> dict[str, Any]:
    keys = _index_keys(client_id=client_id, phone=phone, tg_nick=tg_nick)
    with _LOCK:
        store = _load()
        tid = _resolve_thread_id(store, keys)
        if not tid:
            return public_thread(None)
        return public_thread(store["threads"].get(tid))


def append_message(
    *,
    client_id: str,
    text: str,
    direction: str = "outbound",
    channel: str = "telegram",
    label: str = "",
    phone: str = "",
    tg_nick: str = "",
    client_name: str = "",
    source: str = "manual",
) -> dict[str, Any]:
    """Append one message to the client thread. Returns public thread."""
    body = (text or "").strip()
    if not body:
        raise ValueError("text required")
    cid = (client_id or "").strip()
    if not cid:
        raise ValueError("client_id required")
    direction = (direction or "outbound").strip().lower()
    if direction not in ("outbound", "inbound", "system"):
        direction = "outbound"
    channel = (channel or "telegram").strip().lower() or "telegram"
    lbl = (label or "").strip() or _channel_label(channel, direction)
    msg = {
        "id": str(uuid.uuid4()),
        "direction": direction,
        "channel": channel,
        "label": lbl,
        "text": body,
        "ts": _now(),
        "source": (source or "manual").strip() or "manual",
    }
    with _LOCK:
        store = _load()
        thread = _ensure_thread(
            store,
            client_id=cid,
            phone=phone,
            tg_nick=tg_nick,
            client_name=client_name,
        )
        messages = list(thread.get("messages") or [])
        messages.append(msg)
        if len(messages) > _MAX_MESSAGES:
            messages = messages[-_MAX_MESSAGES:]
        thread["messages"] = messages
        thread["updated_at"] = msg["ts"]
        _save(store)
        return public_thread(thread)


def seed_from_moysklad_attr(
    *,
    client_id: str,
    attr_value: str,
    phone: str = "",
    tg_nick: str = "",
    client_name: str = "",
) -> dict[str, Any]:
    """If local thread empty and MoySklad attr has non-URL text, import once."""
    raw = (attr_value or "").strip()
    if not raw or _URL_RE.match(raw):
        return get_thread(client_id=client_id, phone=phone, tg_nick=tg_nick)
    with _LOCK:
        store = _load()
        keys = _index_keys(client_id=client_id, phone=phone, tg_nick=tg_nick)
        tid = _resolve_thread_id(store, keys)
        if tid:
            existing = store["threads"].get(tid) or {}
            if existing.get("messages"):
                return public_thread(existing)
        thread = _ensure_thread(
            store,
            client_id=client_id,
            phone=phone,
            tg_nick=tg_nick,
            client_name=client_name,
        )
        if thread.get("messages"):
            return public_thread(thread)
        thread["messages"] = [
            {
                "id": str(uuid.uuid4()),
                "direction": "system",
                "channel": "telegram",
                "label": "импорт · MoySklad",
                "text": raw[:4000],
                "ts": _now(),
                "source": "moysklad_attr",
            }
        ]
        thread["updated_at"] = _now()
        _save(store)
        return public_thread(thread)


def enrich_client_row(client: dict[str, Any]) -> dict[str, Any]:
    """Attach conversation preview onto a public client dict (table row)."""
    if not isinstance(client, dict):
        return client
    try:
        from plugins.moysklad.telegram_export import apply_export_overlay_to_public

        client = apply_export_overlay_to_public(client)
    except Exception:
        pass
    cid = str(client.get("id") or "").strip()
    phone = str(client.get("phone") or "")
    tg_nick = str(client.get("tg_nick") or "")
    attr = str(client.get("tg_conversation") or "")
    thread = seed_from_moysklad_attr(
        client_id=cid,
        attr_value=attr,
        phone=phone,
        tg_nick=tg_nick,
        client_name=str(client.get("name") or ""),
    )
    preview = thread.get("preview") or ""
    out = dict(client)
    out["tg_conversation_attr"] = attr
    out["tg_conversation_preview"] = preview
    out["conversation_count"] = int(thread.get("message_count") or 0)
    if not out.get("tg_chat_id"):
        tid_chat = thread.get("tg_chat_id") if isinstance(thread, dict) else None
        if tid_chat:
            out["tg_chat_id"] = tid_chat
    # Column «TG conversation»: prefer live/local history preview.
    if preview:
        out["tg_conversation"] = preview
    elif attr and not _URL_RE.match(attr):
        out["tg_conversation"] = attr[:96] + ("…" if len(attr) > 96 else "")
    elif attr:
        out["tg_conversation"] = attr  # deep-link fallback
    else:
        out["tg_conversation"] = ""
    return out


def enrich_clients(clients: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [enrich_client_row(c) for c in (clients or [])]


def conversation_for_detail(detail: dict[str, Any]) -> dict[str, Any]:
    """Resolve + seed thread for a client-card detail payload."""
    client = detail.get("client") or {}
    return seed_from_moysklad_attr(
        client_id=str(client.get("id") or ""),
        attr_value=str(client.get("tg_conversation") or ""),
        phone=str(client.get("phone") or ""),
        tg_nick=str(client.get("tg_nick") or ""),
        client_name=str(client.get("name") or ""),
    )


def _session_blob(session: dict[str, Any]) -> str:
    parts = [
        session.get("title"),
        session.get("display_name"),
        session.get("chat_id"),
        session.get("user_id"),
        session.get("id"),
    ]
    origin = session.get("origin_json") or ""
    if isinstance(origin, dict):
        parts.extend(str(v) for v in origin.values())
    else:
        parts.append(str(origin))
    return " ".join(str(p or "") for p in parts).lower()


def _role_to_direction(role: str) -> str:
    r = (role or "").strip().lower()
    if r in ("user", "human", "inbound"):
        return "inbound"
    if r in ("system", "tool"):
        return "system"
    return "outbound"


def sync_from_gateway(
    *,
    client_id: str,
    phone: str = "",
    tg_nick: str = "",
    client_name: str = "",
    limit_sessions: int = 40,
    limit_messages: int = 80,
) -> dict[str, Any]:
    """Best-effort pull of Telegram gateway session history into local store.

    Matches sessions by ``tg_nick`` / phone digits against title, display_name,
    chat_id, and origin metadata. Returns public thread + sync meta.
    """
    cid = (client_id or "").strip()
    if not cid:
        raise ValueError("client_id required")
    nick = normalize_tg_nick(tg_nick)
    digits = normalize_phone(phone)
    imported = 0
    matched_sessions = 0
    error = ""
    try:
        from hermes_state import SessionDB

        db = SessionDB()
        sessions = db.search_sessions(source="telegram", limit=max(1, int(limit_sessions)))
        needles = [n for n in (nick, digits) if n]
        if not needles:
            return {
                **get_thread(client_id=cid, phone=phone, tg_nick=tg_nick),
                "sync": {
                    "ok": False,
                    "reason": "no_tg_nick_or_phone",
                    "imported": 0,
                    "matched_sessions": 0,
                },
            }
        hits: list[dict[str, Any]] = []
        for session in sessions or []:
            blob = _session_blob(session)
            if any(n in blob for n in needles):
                hits.append(session)
        matched_sessions = len(hits)
        with _LOCK:
            store = _load()
            thread = _ensure_thread(
                store,
                client_id=cid,
                phone=phone,
                tg_nick=tg_nick,
                client_name=client_name,
            )
            existing = list(thread.get("messages") or [])
            existing_keys = {
                (
                    str(m.get("direction") or ""),
                    str(m.get("text") or "").strip()[:200],
                    str(m.get("ts") or "")[:19],
                )
                for m in existing
            }
            for session in hits:
                sid = str(session.get("id") or "")
                if not sid:
                    continue
                try:
                    messages = db.get_messages(sid, limit=max(1, int(limit_messages)))
                except Exception:
                    continue
                for msg in messages or []:
                    role = str(msg.get("role") or "")
                    text = str(msg.get("content") or "").strip()
                    if not text or role == "tool":
                        continue
                    # Skip huge tool dumps / JSON blobs.
                    if text.startswith("{") and len(text) > 400:
                        continue
                    direction = _role_to_direction(role)
                    ts = str(msg.get("timestamp") or msg.get("created_at") or _now())
                    key = (direction, text[:200], ts[:19])
                    if key in existing_keys:
                        continue
                    existing_keys.add(key)
                    existing.append({
                        "id": str(uuid.uuid4()),
                        "direction": direction,
                        "channel": "telegram",
                        "label": _channel_label("telegram", direction) + " · gateway",
                        "text": text[:4000],
                        "ts": ts if "T" in ts else _now(),
                        "source": "gateway_telegram",
                        "session_id": sid,
                    })
                    imported += 1
            if len(existing) > _MAX_MESSAGES:
                existing = existing[-_MAX_MESSAGES:]
            # Stable chronological order when timestamps allow.
            existing.sort(key=lambda m: str(m.get("ts") or ""))
            thread["messages"] = existing
            if imported:
                thread["updated_at"] = _now()
            _save(store)
            public = public_thread(thread)
    except Exception as exc:
        error = str(exc)
        public = get_thread(client_id=cid, phone=phone, tg_nick=tg_nick)
    public["sync"] = {
        "ok": not error,
        "imported": imported,
        "matched_sessions": matched_sessions,
        "error": error or None,
        "source": "gateway_telegram",
    }
    return public

