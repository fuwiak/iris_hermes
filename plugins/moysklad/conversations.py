"""Per-client messaging thread store for MoySklad CRM.

Persistence ladder (same idea as catalog / ai_fill / telegram_export overlay):

1. Redis — when ``REDIS_URL`` / ``MOYSKLAD_REDIS_URL`` is set
2. File ``$HERMES_HOME/moysklad/conversations.json``
3. Process-local memory

Linked by client_id, normalized phone, and Telegram nick. Sync pulls Hermes
gateway Telegram sessions **and** the personal MTProto account history
(``telegram_user``) so inbound replies land in the same thread for AI.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from hermes_constants import get_hermes_home

log = logging.getLogger(__name__)

_LOCK = threading.Lock()
_PHONE_RE = re.compile(r"\D+")
_URL_RE = re.compile(r"^https?://|^tg:", re.IGNORECASE)
_MEMORY_STORE: dict[str, Any] | None = None
_MEMORY_FP: str | None = None
_CONV_REDIS_KEY = "moysklad:conversations:v1"
DEFAULT_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days

# Soft cap so the JSON store stays readable in UI / AI facts.
_MAX_MESSAGES = 200

# Soft throttle so Facts / card open do not hammer MTProto on every paint.
_LIVE_PULL_TTL_SECONDS = 90.0
_LIVE_PULL_AT: dict[str, float] = {}


def cache_ttl_seconds() -> int:
    raw = (os.environ.get("MOYSKLAD_CONVERSATIONS_TTL_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_TTL_SECONDS
    try:
        return max(3600, int(raw))
    except ValueError:
        return DEFAULT_TTL_SECONDS


def _redis_url() -> str:
    return (os.environ.get("REDIS_URL") or os.environ.get("MOYSKLAD_REDIS_URL") or "").strip()


def _redis_client():
    url = _redis_url()
    if not url:
        return None
    try:
        import redis  # type: ignore[import-not-found]
    except Exception:
        log.debug("REDIS_URL set but redis package missing; conversations file cache")
        return None
    try:
        client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2.0)
        client.ping()
        return client
    except Exception as exc:
        log.warning("MoySklad conversations Redis unavailable (%s); file cache", exc)
        return None


def cache_backend_name() -> str:
    if _redis_client() is not None:
        return "redis+file"
    return "file"


def _account_fingerprint() -> str:
    token = (os.environ.get("MOYSKLAD_API_TOKEN") or "").strip()
    if not token:
        return "no-token"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _redis_key() -> str:
    return f"{_CONV_REDIS_KEY}:{_account_fingerprint()}"


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


def _memory_fingerprint() -> str:
    return f"{_account_fingerprint()}:{_store_path()}"


def clear_memory_for_tests() -> None:
    """Drop process-local cache (tests / HERMES_HOME switches)."""
    global _MEMORY_STORE, _MEMORY_FP
    _MEMORY_STORE = None
    _MEMORY_FP = None
    _LIVE_PULL_AT.clear()


def _load() -> dict[str, Any]:
    """Load store: memory → Redis → file."""
    global _MEMORY_STORE, _MEMORY_FP
    fp = _memory_fingerprint()
    if (
        _MEMORY_FP == fp
        and isinstance(_MEMORY_STORE, dict)
        and isinstance(_MEMORY_STORE.get("threads"), dict)
    ):
        return _MEMORY_STORE

    client = _redis_client()
    if client is not None:
        try:
            raw = client.get(_redis_key())
            if raw:
                data = json.loads(raw)
                if isinstance(data, dict):
                    threads = data.get("threads") if isinstance(data.get("threads"), dict) else {}
                    index = data.get("index") if isinstance(data.get("index"), dict) else {}
                    store = {"threads": threads, "index": index}
                    _MEMORY_STORE = store
                    _MEMORY_FP = fp
                    return store
        except Exception as exc:
            log.warning("MoySklad conversations Redis get failed: %s", exc)

    path = _store_path()
    if not path.is_file():
        store = _empty_store()
        _MEMORY_STORE = store
        _MEMORY_FP = fp
        return store
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        store = _empty_store()
        _MEMORY_STORE = store
        _MEMORY_FP = fp
        return store
    if not isinstance(raw, dict):
        store = _empty_store()
        _MEMORY_STORE = store
        _MEMORY_FP = fp
        return store
    threads = raw.get("threads") if isinstance(raw.get("threads"), dict) else {}
    index = raw.get("index") if isinstance(raw.get("index"), dict) else {}
    store = {"threads": threads, "index": index}
    _MEMORY_STORE = store
    _MEMORY_FP = fp
    return store


def _save(store: dict[str, Any]) -> None:
    """Persist store to memory + Redis + file."""
    global _MEMORY_STORE, _MEMORY_FP
    payload = {
        "threads": store.get("threads") if isinstance(store.get("threads"), dict) else {},
        "index": store.get("index") if isinstance(store.get("index"), dict) else {},
        "saved_at": time.time(),
        "cache_backend": cache_backend_name(),
    }
    _MEMORY_STORE = {
        "threads": payload["threads"],
        "index": payload["index"],
    }
    _MEMORY_FP = _memory_fingerprint()
    ttl = cache_ttl_seconds()

    client = _redis_client()
    if client is not None:
        try:
            client.setex(
                _redis_key(),
                ttl,
                json.dumps(payload, ensure_ascii=False, default=str),
            )
        except Exception as exc:
            log.warning("MoySklad conversations Redis set failed: %s", exc)

    path = _store_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
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
    client_name: str = "",
) -> dict[str, Any]:
    keys = _index_keys(client_id=client_id, phone=phone, tg_nick=tg_nick)
    with _LOCK:
        store = _load()
        tid = _resolve_thread_id(store, keys)
        if not tid and (client_name or "").strip():
            # Soft match by name — Telegram export often indexes by display name
            # when phone/nick are empty (e.g. client «анатолий»).
            needle = (
                str(client_name)
                .strip()
                .lower()
                .replace("ё", "е")
            )
            if needle:
                for candidate_id, thread in (store.get("threads") or {}).items():
                    if not isinstance(thread, dict):
                        continue
                    name = (
                        str(thread.get("client_name") or "")
                        .strip()
                        .lower()
                        .replace("ё", "е")
                    )
                    if name and (name == needle or needle in name or name in needle):
                        tid = str(candidate_id)
                        break
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
    tg_chat_id: str = "",
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
        chat = str(tg_chat_id or "").strip()
        if chat:
            thread["tg_chat_id"] = chat
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
        return get_thread(
            client_id=client_id,
            phone=phone,
            tg_nick=tg_nick,
            client_name=client_name,
        )
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


# Soft throttle so Facts / card open do not hammer MTProto on every paint.
# (TTL / stamp dict live near top of module — see _LIVE_PULL_*.)


def conversation_for_detail(
    detail: dict[str, Any],
    *,
    pull_live: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    """Resolve thread for a client-card / Facts payload.

    Default ``pull_live=True``: best-effort sync of gateway + personal MTProto
    history so replies after a mass Рассылка land in «TG conversation» (throttled
    per client). Pass ``pull_live=False`` for cheap seed-only reads.
    """
    client = detail.get("client") or {}
    cid = str(client.get("id") or "").strip()
    phone = str(client.get("phone") or "")
    tg_nick = str(client.get("tg_nick") or "")
    tg_chat_id = str(client.get("tg_chat_id") or "")
    client_name = str(client.get("name") or "")
    if not pull_live or not cid:
        return seed_from_moysklad_attr(
            client_id=cid,
            attr_value=str(client.get("tg_conversation") or ""),
            phone=phone,
            tg_nick=tg_nick,
            client_name=client_name,
        )
    now = time.time()
    last = _LIVE_PULL_AT.get(cid)
    if not force and last is not None and (now - last) < _LIVE_PULL_TTL_SECONDS:
        # Recent live pull already ran — serve local store (incl. inbound).
        local = get_thread(
            client_id=cid,
            phone=phone,
            tg_nick=tg_nick,
            client_name=client_name,
        )
        if int(local.get("message_count") or 0) > 0:
            return local
        return seed_from_moysklad_attr(
            client_id=cid,
            attr_value=str(client.get("tg_conversation") or ""),
            phone=phone,
            tg_nick=tg_nick,
            client_name=client_name,
        )
    try:
        _LIVE_PULL_AT[cid] = now
        thread = sync_client_conversation(
            client_id=cid,
            phone=phone,
            tg_nick=tg_nick,
            tg_chat_id=tg_chat_id,
            client_name=client_name,
        )
        # If live sync found nothing, keep MoySklad attr seed.
        if int(thread.get("message_count") or 0) == 0:
            return seed_from_moysklad_attr(
                client_id=cid,
                attr_value=str(client.get("tg_conversation") or ""),
                phone=phone,
                tg_nick=tg_nick,
                client_name=client_name,
            )
        return thread
    except Exception:
        log.debug("conversation_for_detail live pull failed", exc_info=True)
        return seed_from_moysklad_attr(
            client_id=cid,
            attr_value=str(client.get("tg_conversation") or ""),
            phone=phone,
            tg_nick=tg_nick,
            client_name=client_name,
        )


def clear_live_pull_throttle_for_tests() -> None:
    _LIVE_PULL_AT.clear()


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
    inbound_imported = 0
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
                    "inbound_imported": 0,
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
                    if direction == "inbound":
                        inbound_imported += 1
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
        "inbound_imported": inbound_imported,
        "matched_sessions": matched_sessions,
        "error": error or None,
        "source": "gateway_telegram",
    }
    return public


def _merge_history_messages(
    *,
    client_id: str,
    phone: str = "",
    tg_nick: str = "",
    client_name: str = "",
    tg_chat_id: str = "",
    rows: list[dict[str, Any]],
    source: str,
    label_suffix: str,
) -> tuple[dict[str, Any], int, int]:
    """Merge normalized history rows into the local thread.

    Returns ``(public_thread, imported, inbound_imported)``.
    """
    cid = (client_id or "").strip()
    imported = 0
    inbound_imported = 0
    with _LOCK:
        store = _load()
        thread = _ensure_thread(
            store,
            client_id=cid,
            phone=phone,
            tg_nick=tg_nick,
            client_name=client_name,
        )
        if tg_chat_id and not thread.get("tg_chat_id"):
            thread["tg_chat_id"] = str(tg_chat_id).strip()
        existing = list(thread.get("messages") or [])
        existing_keys = {
            (
                str(m.get("direction") or ""),
                str(m.get("text") or "").strip()[:200],
                str(m.get("ts") or "")[:19],
            )
            for m in existing
        }
        for raw in rows or []:
            text = str(raw.get("text") or "").strip()
            if not text:
                continue
            direction = str(raw.get("direction") or "inbound").strip().lower()
            if direction not in ("outbound", "inbound", "system"):
                direction = "inbound"
            ts = str(raw.get("ts") or _now())
            key = (direction, text[:200], ts[:19])
            if key in existing_keys:
                continue
            existing_keys.add(key)
            existing.append(
                {
                    "id": str(uuid.uuid4()),
                    "direction": direction,
                    "channel": "telegram",
                    "label": _channel_label("telegram", direction) + label_suffix,
                    "text": text[:4000],
                    "ts": ts if "T" in ts else _now(),
                    "source": source,
                    "message_id": raw.get("message_id"),
                }
            )
            imported += 1
            if direction == "inbound":
                inbound_imported += 1
        if len(existing) > _MAX_MESSAGES:
            existing = existing[-_MAX_MESSAGES:]
        existing.sort(key=lambda m: str(m.get("ts") or ""))
        thread["messages"] = existing
        if imported:
            thread["updated_at"] = _now()
        _save(store)
        return public_thread(thread), imported, inbound_imported


def sync_from_telegram_user(
    *,
    client_id: str,
    phone: str = "",
    tg_nick: str = "",
    tg_chat_id: str = "",
    client_name: str = "",
    limit: int = 40,
) -> dict[str, Any]:
    """Pull personal MTProto chat history into the local client thread.

    Accounts for inbound replies on the operator's own Telegram account
    (the one used for mass Рассылки) — Bot/gateway sessions alone miss them.
    """
    cid = (client_id or "").strip()
    if not cid:
        raise ValueError("client_id required")
    nick = normalize_tg_nick(tg_nick)
    digits = normalize_phone(phone)
    chat_id = str(tg_chat_id or "").strip()
    peer = chat_id or (f"@{nick}" if nick else "") or digits
    if not peer:
        public = get_thread(client_id=cid, phone=phone, tg_nick=tg_nick)
        public["sync"] = {
            "ok": False,
            "reason": "no_tg_nick_or_phone",
            "imported": 0,
            "inbound_imported": 0,
            "source": "telegram_user",
        }
        return public

    try:
        from plugins.platforms.telegram_user import client as tg_user

        hist = tg_user.fetch_history(peer=peer, limit=limit)
    except Exception as exc:
        public = get_thread(client_id=cid, phone=phone, tg_nick=tg_nick)
        public["sync"] = {
            "ok": False,
            "imported": 0,
            "inbound_imported": 0,
            "error": str(exc),
            "source": "telegram_user",
        }
        return public

    if not hist.get("ok"):
        public = get_thread(client_id=cid, phone=phone, tg_nick=tg_nick)
        public["sync"] = {
            "ok": False,
            "imported": 0,
            "inbound_imported": 0,
            "error": hist.get("detail") or hist.get("error") or "history_failed",
            "reason": hist.get("error") or "history_failed",
            "source": "telegram_user",
        }
        return public

    rows = list(hist.get("messages") or [])
    resolved_chat = str(hist.get("tg_chat_id") or chat_id or "").strip()
    resolved_nick = normalize_tg_nick(str(hist.get("tg_nick") or nick or ""))
    public, imported, inbound_imported = _merge_history_messages(
        client_id=cid,
        phone=phone,
        tg_nick=resolved_nick or nick,
        client_name=client_name,
        tg_chat_id=resolved_chat,
        rows=rows,
        source="telegram_user",
        label_suffix=" · личный TG",
    )
    public["sync"] = {
        "ok": True,
        "imported": imported,
        "inbound_imported": inbound_imported,
        "fetched": len(rows),
        "source": "telegram_user",
        "via": hist.get("via"),
    }
    return public


# Peers that errored recently (privacy, deleted account) — skip until backoff
# expires so bulk runs do not re-hammer the same dead targets. Process-local.
_DIALOG_FAILED_AT: dict[str, float] = {}


def _dialog_sync_fresh(
    store: dict[str, Any],
    *,
    client_id: str,
    phone: str,
    tg_nick: str,
    now: float,
    min_age_seconds: float,
) -> bool:
    keys = _index_keys(client_id=client_id, phone=phone, tg_nick=tg_nick)
    tid = _resolve_thread_id(store, keys)
    if not tid:
        return False
    thread = (store.get("threads") or {}).get(tid)
    if not isinstance(thread, dict):
        return False
    stamped = float(thread.get("dialog_synced_at") or 0)
    return stamped > 0 and (now - stamped) < min_age_seconds


def _stamp_dialog_synced(*, client_id: str, phone: str, tg_nick: str) -> None:
    with _LOCK:
        store = _load()
        keys = _index_keys(client_id=client_id, phone=phone, tg_nick=tg_nick)
        tid = _resolve_thread_id(store, keys)
        if not tid:
            return
        thread = (store.get("threads") or {}).get(tid)
        if isinstance(thread, dict):
            thread["dialog_synced_at"] = time.time()
            _save(store)


def sync_telegram_dialogs_into_threads(
    rows: list[dict[str, Any]],
    *,
    max_peers: int = 40,
    per_chat_limit: int = 20,
    min_age_seconds: float = 6 * 3600.0,
) -> dict[str, Any]:
    """Bulk-sync the operator's personal Telegram into the «TG conversation»
    column: match cached TG peers (address book + dialogs) against catalog
    rows by chat id / @nick / phone, pull recent history for matches into
    local threads. Per-card Sync stays the live path; this keeps the Клиенты
    column populated without opening each card.

    Threads stamp ``dialog_synced_at`` so repeat runs skip fresh peers —
    successive keep-warm passes walk further down the match list.
    """
    try:
        from plugins.platforms.telegram_user import client as tg_user
    except Exception as exc:  # pragma: no cover — import env issue
        return {"ok": False, "error": f"telegram_user unavailable: {exc}"}

    try:
        contacts = list(tg_user.cached_contacts() or [])
    except Exception:
        contacts = []
    if not contacts:
        return {
            "ok": False,
            "error": "no_cached_contacts",
            "detail": "Сначала подключите личный Telegram и синхронизируйте контакты (Рассылки).",
        }

    by_chat: dict[str, dict[str, str]] = {}
    by_nick: dict[str, dict[str, str]] = {}
    by_phone: dict[str, dict[str, str]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        rid = str(row.get("_moysklad_id") or row.get("id") or "").strip()
        if not rid:
            continue
        info = {
            "client_id": rid,
            "name": str(row.get("Наименование") or row.get("name") or ""),
            "phone": str(row.get("Телефон") or row.get("phone") or ""),
            "tg_nick": str(row.get("ТГ ник") or row.get("tg_nick") or ""),
            "tg_chat_id": str(row.get("ТГ chat id") or row.get("tg_chat_id") or ""),
        }
        chat = info["tg_chat_id"].strip()
        nick = normalize_tg_nick(info["tg_nick"])
        digits = normalize_phone(info["phone"])
        if chat:
            by_chat.setdefault(chat, info)
        if nick:
            by_nick.setdefault(nick, info)
        if digits:
            by_phone.setdefault(digits, info)

    # Dialog peers first — people the operator actually talks to.
    ordered = sorted(
        contacts,
        key=lambda c: 0
        if str(c.get("peer_source") or c.get("source") or "") == "dialog"
        else 1,
    )
    seen: set[str] = set()
    candidates: list[tuple[dict[str, str], dict[str, str]]] = []
    for contact in ordered:
        if not isinstance(contact, dict):
            continue
        chat = str(contact.get("tg_chat_id") or contact.get("id") or "").strip()
        nick = normalize_tg_nick(str(contact.get("tg_nick") or ""))
        digits = normalize_phone(str(contact.get("phone") or ""))
        info = (
            (by_chat.get(chat) if chat else None)
            or (by_nick.get(nick) if nick else None)
            or (by_phone.get(digits) if digits else None)
        )
        if not info or info["client_id"] in seen:
            continue
        seen.add(info["client_id"])
        candidates.append((info, {"tg_chat_id": chat, "tg_nick": nick}))

    now = time.time()
    with _LOCK:
        store = _load()
        pending = [
            (info, peer)
            for info, peer in candidates
            if not _dialog_sync_fresh(
                store,
                client_id=info["client_id"],
                phone=info["phone"],
                tg_nick=peer["tg_nick"] or info["tg_nick"],
                now=now,
                min_age_seconds=min_age_seconds,
            )
        ]
    pending = [
        p
        for p in pending
        if (now - _DIALOG_FAILED_AT.get(p[0]["client_id"], 0.0)) >= min_age_seconds
    ]

    stats: dict[str, Any] = {
        "ok": True,
        "contacts": len(contacts),
        "matched": len(candidates),
        "skipped_fresh": len(candidates) - len(pending),
        "attempted": 0,
        "synced": 0,
        "imported": 0,
        "inbound_imported": 0,
        "errors": 0,
    }
    cap = max(0, int(max_peers))
    for info, peer in pending[:cap]:
        stats["attempted"] += 1
        try:
            thread = sync_from_telegram_user(
                client_id=info["client_id"],
                phone=info["phone"],
                tg_nick=peer["tg_nick"] or info["tg_nick"],
                tg_chat_id=peer["tg_chat_id"] or info["tg_chat_id"],
                client_name=info["name"],
                limit=per_chat_limit,
            )
            sync_meta = thread.get("sync") or {}
            if sync_meta.get("ok"):
                stats["synced"] += 1
                stats["imported"] += int(sync_meta.get("imported") or 0)
                stats["inbound_imported"] += int(sync_meta.get("inbound_imported") or 0)
                _stamp_dialog_synced(
                    client_id=info["client_id"],
                    phone=info["phone"],
                    tg_nick=peer["tg_nick"] or info["tg_nick"],
                )
            else:
                stats["errors"] += 1
                _DIALOG_FAILED_AT[info["client_id"]] = time.time()
        except Exception:
            log.warning(
                "telegram dialog sync failed for %s", info["client_id"], exc_info=True
            )
            stats["errors"] += 1
            _DIALOG_FAILED_AT[info["client_id"]] = time.time()
        # Gentle pacing — MTProto/gateway flood control.
        time.sleep(0.2)
    stats["pending_left"] = max(0, len(pending) - stats["attempted"])
    return stats


def clear_dialog_sync_backoff_for_tests() -> None:
    _DIALOG_FAILED_AT.clear()


def sync_client_conversation(
    *,
    client_id: str,
    phone: str = "",
    tg_nick: str = "",
    tg_chat_id: str = "",
    client_name: str = "",
) -> dict[str, Any]:
    """Gateway sessions + personal MTProto history → one local thread."""
    gateway = sync_from_gateway(
        client_id=client_id,
        phone=phone,
        tg_nick=tg_nick,
        client_name=client_name,
    )
    user = sync_from_telegram_user(
        client_id=client_id,
        phone=phone,
        tg_nick=tg_nick,
        tg_chat_id=tg_chat_id,
        client_name=client_name,
    )
    # Prefer the richer merged thread (user sync runs second and reloads store).
    thread = user if int((user.get("message_count") or 0)) >= int(
        (gateway.get("message_count") or 0)
    ) else gateway
    # If user sync failed soft, still surface gateway messages.
    if user.get("empty") and not gateway.get("empty"):
        thread = gateway
    g_sync = gateway.get("sync") or {}
    u_sync = user.get("sync") or {}
    imported = int(g_sync.get("imported") or 0) + int(u_sync.get("imported") or 0)
    inbound = int(g_sync.get("inbound_imported") or 0) + int(
        u_sync.get("inbound_imported") or 0
    )
    thread = dict(thread)
    thread["sync"] = {
        "ok": bool(g_sync.get("ok") or u_sync.get("ok")),
        "imported": imported,
        "inbound_imported": inbound,
        "gateway": g_sync,
        "telegram_user": u_sync,
        "source": "gateway+telegram_user",
    }
    cid = (client_id or "").strip()
    if cid:
        _LIVE_PULL_AT[cid] = time.time()
    return thread


def _last_human_direction(thread: dict[str, Any] | None) -> str:
    """Last outbound/inbound direction — system noise ignored."""
    if not isinstance(thread, dict):
        return ""
    for msg in reversed(list(thread.get("messages") or [])):
        if not isinstance(msg, dict):
            continue
        direction = str(msg.get("direction") or "").strip().lower()
        if direction in ("outbound", "inbound"):
            return direction
    return ""


def list_awaiting_replies(
    client_ids: list[str] | None = None,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Threads where the client spoke last (needs operator follow-up).

    Used after a mass Рассылка: collect inbound answers without opening
    each card. Optional ``client_ids`` scopes to the last send cohort;
    empty → scan the whole store (capped by ``limit``).
    """
    want: set[str] | None = None
    if client_ids:
        want = {str(x or "").strip() for x in client_ids if str(x or "").strip()}
        if not want:
            return []
    try:
        cap = max(1, min(int(limit), 2000))
    except (TypeError, ValueError):
        cap = 200

    with _LOCK:
        store = _load()
        threads = store.get("threads") or {}
        rows: list[dict[str, Any]] = []
        for tid, thread in threads.items():
            if not isinstance(thread, dict):
                continue
            cid = str(thread.get("client_id") or tid or "").strip()
            if want is not None and cid not in want and str(tid) not in want:
                continue
            if _last_human_direction(thread) != "inbound":
                continue
            public = public_thread(thread)
            rows.append(
                {
                    "client_id": cid,
                    "client_name": public.get("client_name") or "",
                    "tg_nick": public.get("tg_nick") or "",
                    "phone": public.get("phone") or "",
                    "preview": public.get("preview") or "",
                    "message_count": public.get("message_count") or 0,
                    "updated_at": public.get("updated_at"),
                    "awaiting_reply": True,
                }
            )

    def _sort_key(row: dict[str, Any]) -> str:
        return str(row.get("updated_at") or "")

    rows.sort(key=_sort_key, reverse=True)
    return rows[:cap]
