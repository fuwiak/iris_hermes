"""Import Telegram Desktop JSON export into MoySklad CRM threads.

Maps ``data/telegram_export.json`` (or ``$HERMES_HOME/moysklad/telegram_export.json``)
personal chats onto catalog clients by phone (via Contacts) and name, then:

* seeds ``conversations`` history (TG conversation column / client card)
* fills empty ``ТГ ник`` when an ``@username`` can be attributed to the peer
* stores ``tg_chat_id`` for Bot API delivery

Persistence ladder for the overlay (same as catalog / ai_fill):

1. Redis — when ``REDIS_URL`` / ``MOYSKLAD_REDIS_URL`` is set
2. File ``$HERMES_HOME/moysklad/telegram_export_overlay.json``
3. Process-local memory

Export has no per-chat username field — nick fill is best-effort from mentions /
t.me links that clearly belong to the peer side.
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
from plugins.moysklad.conversations import (
    _MAX_MESSAGES,
    _LOCK as _CONV_LOCK,
    _ensure_thread,
    _load as _conv_load,
    _now,
    _save as _conv_save,
    normalize_tg_nick,
    preview_text,
)
from plugins.moysklad.dedupe import normalize_name, normalize_phone as dedupe_phone

log = logging.getLogger(__name__)

_OVERLAY_NAME = "telegram_export_overlay.json"
_OVERLAY_REDIS_KEY = "moysklad:telegram_export:overlay:v1"
_IMPORT_LOCK = threading.Lock()
_CACHE_LOCK = threading.RLock()
_IMPORT_DONE_FOR: set[str] = set()
_OVERLAY_MEMORY: dict[str, Any] | None = None
_OVERLAY_MEMORY_AT = 0.0
_OVERLAY_MEMORY_FP: str | None = None

DEFAULT_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days

_TME_RE = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z0-9_]{4,64})",
    re.IGNORECASE,
)
_FROM_ID_RE = re.compile(r"^user(\d+)$", re.IGNORECASE)

# Telegram display names are typed by the client, MoySklad names by the studio —
# they routinely disagree only by lookalike letters («Viсtoria» with a Cyrillic
# «с»). Fold both sides onto one alphabet before comparing.
_CONFUSABLES = str.maketrans({
    "а": "a", "с": "c", "е": "e", "о": "o", "р": "p", "х": "x", "у": "y",
    "к": "k", "м": "m", "н": "h", "т": "t", "в": "b", "і": "i", "ѕ": "s",
    "ј": "j", "ԁ": "d", "ё": "e",
})

# Mentions that are never the client peer (studio / bots / suppliers).
_SKIP_NICKS = frozenset({
    "veresk_flowers_msk",
    "get_myidrobot",
    "premiumrussia",
    "russian_seller",
    "cvetioptomru",
})


def fold_name(value: Any) -> str:
    """Normalized name with Cyrillic/Latin lookalikes folded onto one alphabet."""
    return normalize_name(value).translate(_CONFUSABLES)


def cache_ttl_seconds() -> int:
    raw = (os.environ.get("MOYSKLAD_TG_EXPORT_TTL_SECONDS") or "").strip()
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
        log.debug("REDIS_URL set but redis package missing; tg export file cache")
        return None
    try:
        client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2.0)
        client.ping()
        return client
    except Exception as exc:
        log.warning("MoySklad tg-export Redis unavailable (%s); file cache", exc)
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


def _overlay_redis_key() -> str:
    return f"{_OVERLAY_REDIS_KEY}:{_account_fingerprint()}"


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
    env = (os.environ.get("MOYSKLAD_TELEGRAM_EXPORT") or "").strip()
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
        "cache_backend": str(raw.get("cache_backend") or ""),
    }


def _overlay_memory_fingerprint() -> str:
    return f"{_account_fingerprint()}:{_overlay_path()}"


def load_overlay() -> dict[str, Any]:
    """Load overlay from memory → Redis → file."""
    global _OVERLAY_MEMORY, _OVERLAY_MEMORY_AT, _OVERLAY_MEMORY_FP
    fp = _overlay_memory_fingerprint()
    with _CACHE_LOCK:
        if (
            _OVERLAY_MEMORY_FP == fp
            and isinstance(_OVERLAY_MEMORY, dict)
            and _OVERLAY_MEMORY.get("by_client_id") is not None
        ):
            return {
                "by_client_id": dict(_OVERLAY_MEMORY.get("by_client_id") or {}),
                "stats": dict(_OVERLAY_MEMORY.get("stats") or {}),
                "saved_at": float(_OVERLAY_MEMORY.get("saved_at") or 0),
                "cache_backend": str(_OVERLAY_MEMORY.get("cache_backend") or "memory"),
            }

    client = _redis_client()
    if client is not None:
        try:
            raw = client.get(_overlay_redis_key())
            if raw:
                overlay = _normalize_overlay(json.loads(raw))
                overlay["cache_backend"] = "redis"
                with _CACHE_LOCK:
                    _OVERLAY_MEMORY = overlay
                    _OVERLAY_MEMORY_AT = time.time()
                    _OVERLAY_MEMORY_FP = fp
                return {
                    "by_client_id": dict(overlay.get("by_client_id") or {}),
                    "stats": dict(overlay.get("stats") or {}),
                    "saved_at": float(overlay.get("saved_at") or 0),
                    "cache_backend": "redis",
                }
        except Exception as exc:
            log.warning("MoySklad tg-export Redis get failed: %s", exc)

    path = _overlay_path()
    if path.is_file():
        try:
            overlay = _normalize_overlay(json.loads(path.read_text(encoding="utf-8")))
            overlay["cache_backend"] = "file"
            with _CACHE_LOCK:
                _OVERLAY_MEMORY = overlay
                _OVERLAY_MEMORY_AT = time.time()
                _OVERLAY_MEMORY_FP = fp
            return {
                "by_client_id": dict(overlay.get("by_client_id") or {}),
                "stats": dict(overlay.get("stats") or {}),
                "saved_at": float(overlay.get("saved_at") or 0),
                "cache_backend": "file",
            }
        except (OSError, json.JSONDecodeError):
            pass
    return _empty_overlay()


def save_overlay(overlay: dict[str, Any]) -> None:
    """Persist overlay to memory + Redis + file."""
    global _OVERLAY_MEMORY, _OVERLAY_MEMORY_AT, _OVERLAY_MEMORY_FP
    payload = _normalize_overlay(overlay)
    payload["saved_at"] = time.time()
    payload["cache_backend"] = cache_backend_name()
    ttl = cache_ttl_seconds()

    with _CACHE_LOCK:
        _OVERLAY_MEMORY = payload
        _OVERLAY_MEMORY_AT = time.time()
        _OVERLAY_MEMORY_FP = _overlay_memory_fingerprint()

    client = _redis_client()
    if client is not None:
        try:
            client.setex(
                _overlay_redis_key(),
                ttl,
                json.dumps(payload, ensure_ascii=False, default=str),
            )
        except Exception as exc:
            log.warning("MoySklad tg-export Redis set failed: %s", exc)

    path = _overlay_path()
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError as exc:
        log.warning("MoySklad tg-export file write failed: %s", exc)


def overlay_for_client(client_id: str) -> dict[str, Any]:
    cid = str(client_id or "").strip()
    if not cid:
        return {}
    entry = (load_overlay().get("by_client_id") or {}).get(cid)
    return dict(entry) if isinstance(entry, dict) else {}


def stamp_catalog_rows_from_overlay(rows: list[dict[str, Any]]) -> int:
    """Write overlay tg fields onto catalog rows (mutates). Returns stamped count."""
    by_id = load_overlay().get("by_client_id") or {}
    if not by_id:
        return 0
    stamped = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("_moysklad_id") or row.get("id") or "").strip()
        entry = by_id.get(cid)
        if not isinstance(entry, dict):
            continue
        changed = False
        nick = str(entry.get("tg_nick") or "").strip()
        if nick and not str(row.get("ТГ ник") or row.get("tg_nick") or "").strip():
            row["ТГ ник"] = nick
            row["tg_nick"] = nick
            changed = True
        chat_id = str(entry.get("tg_chat_id") or "").strip()
        if chat_id and not str(row.get("tg_chat_id") or "").strip():
            row["tg_chat_id"] = chat_id
            changed = True
        preview = str(entry.get("preview") or "").strip()
        if preview and not str(row.get("TG conversation") or row.get("tg_conversation") or "").strip():
            row["TG conversation"] = preview
            row["tg_conversation"] = preview
            changed = True
        if entry.get("message_count") is not None:
            row["conversation_count"] = int(entry.get("message_count") or 0)
        if changed:
            stamped += 1
    return stamped


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


_PHONE_IN_TEXT_RE = re.compile(
    r"(?:\+?7|8)[\s\-(]*\d{3}[\s\-)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}"
)


def _phones_in_messages(
    messages: list[dict[str, Any]], *, limit: int = 6
) -> list[str]:
    """Phone keys the peer typed into the chat — clients state them constantly."""
    found: list[str] = []
    for msg in messages:
        blob = str(msg.get("text") or "")
        if not blob:
            continue
        for raw in _PHONE_IN_TEXT_RE.findall(blob):
            key = dedupe_phone(raw)
            if key and key not in found:
                found.append(key)
                if len(found) >= limit:
                    return found
    return found


def _norm_phone_export(value: Any) -> str:
    """Export phones often look like ``0079…`` — fold into dedupe 10-digit key."""
    digits = re.sub(r"\D+", "", str(value or ""))
    if digits.startswith("00"):
        digits = digits[2:]
    return dedupe_phone(digits)


def _contact_phone_by_name(export: dict[str, Any]) -> dict[str, str]:
    """Folded contact name → phone key. Also indexes the first name alone.

    Chat titles carry whatever the peer set as their display name, so the saved
    address-book entry «Мария Букет» has to be findable from a chat called just
    «Мария» — the single-name key is only kept when it is unambiguous.
    """
    out: dict[str, str] = {}
    first_only: dict[str, str] = {}
    ambiguous: set[str] = set()
    contacts = (export.get("contacts") or {}).get("list") or []
    if not isinstance(contacts, list):
        return out
    for c in contacts:
        if not isinstance(c, dict):
            continue
        first = str(c.get("first_name") or "").strip()
        last = str(c.get("last_name") or "").strip()
        name = fold_name(" ".join(x for x in (first, last) if x))
        phone = _norm_phone_export(c.get("phone_number"))
        if not phone:
            continue
        if name and name not in out:
            out[name] = phone
        short = fold_name(first)
        if not short or short == name:
            continue
        if short in first_only and first_only[short] != phone:
            ambiguous.add(short)
        else:
            first_only[short] = phone
    for short, phone in first_only.items():
        if short not in ambiguous and short not in out:
            out[short] = phone
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
        if from_id == studio_id:
            direction = "outbound"
        elif from_id:
            direction = "inbound"
        else:
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
        name = fold_name(row.get("Наименование") or row.get("name"))
        if name:
            by_name.setdefault(name, []).append(row)
    return by_phone, by_name


def _index_catalog_nicks(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Folded ``ТГ ник`` → row. A nick already on the card is a hard match."""
    by_nick: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        nick = normalize_tg_nick(row.get("ТГ ник") or row.get("tg_nick") or "")
        if nick and nick not in by_nick:
            by_nick[nick] = row
    return by_nick


def _match_row(
    *,
    chat_name: str,
    phone: str,
    by_phone: dict[str, dict[str, Any]],
    by_name: dict[str, list[dict[str, Any]]],
    tg_nick: str = "",
    by_nick: dict[str, dict[str, Any]] | None = None,
    text_phones: list[str] | None = None,
) -> Optional[dict[str, Any]]:
    """Attach one export chat to a catalog row. Strongest signal wins.

    Order: address-book phone → @nick on the card → phone the peer typed into
    the chat → exact folded name → unambiguous substring of a folded name.
    """
    if phone and phone in by_phone:
        return by_phone[phone]

    nick = normalize_tg_nick(tg_nick)
    if nick and by_nick:
        hit = by_nick.get(nick)
        if hit is not None:
            return hit

    for candidate in text_phones or []:
        if candidate in by_phone:
            return by_phone[candidate]

    name = fold_name(chat_name)
    if not name:
        return None
    hits = by_name.get(name) or []
    if len(hits) == 1:
        return hits[0]
    if hits:
        # Same name on several cards — ambiguous, never guess.
        return None
    soft: list[dict[str, Any]] = []
    for key, rows in by_name.items():
        if name in key or key in name:
            soft.extend(rows)
    if len(soft) == 1:
        return soft[0]
    return None


def import_export_into_catalog(
    rows: list[dict[str, Any]],
    *,
    export_path: str | Path | None = None,
    force: bool = False,
    export_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Match export chats → catalog rows; write conversations + cached overlay.

    Idempotent per ``HERMES_HOME`` fingerprint unless ``force=True``.
    """
    path = resolve_export_path(export_path)
    if path is None:
        return {
            "ok": False,
            "error": "export_not_found",
            "matched": 0,
            "imported_messages": 0,
            "cache_backend": cache_backend_name(),
        }

    fp = f"{path}:{path.stat().st_mtime_ns}:{len(rows)}"
    with _IMPORT_LOCK:
        if not force and fp in _IMPORT_DONE_FOR:
            overlay = load_overlay()
            stamped = stamp_catalog_rows_from_overlay(rows)
            return {
                "ok": True,
                "skipped": True,
                "path": str(path),
                "stamped_rows": stamped,
                "cache_backend": cache_backend_name(),
                **(overlay.get("stats") or {}),
            }

        if export_data is not None:
            export = export_data
        else:
            try:
                export = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                return {"ok": False, "error": str(exc), "matched": 0}

        studio_id = _studio_user_id(export)
        phone_by_name = _contact_phone_by_name(export)
        by_phone, by_name = _index_catalog_rows(rows)
        by_nick = _index_catalog_nicks(rows)
        chats = (export.get("chats") or {}).get("list") or []
        if not isinstance(chats, list):
            chats = []

        overlay = load_overlay()
        by_client: dict[str, Any] = dict(overlay.get("by_client_id") or {})
        matched = 0
        imported_messages = 0
        nick_filled = 0

        with _CONV_LOCK:
            store = _conv_load()
            for chat in chats:
                if not isinstance(chat, dict):
                    continue
                if str(chat.get("type") or "") != "personal_chat":
                    continue
                chat_name = str(chat.get("name") or "").strip()
                phone = phone_by_name.get(fold_name(chat_name), "")
                peer_id = _peer_user_id(chat, studio_id)
                nick = _extract_peer_nick(chat, studio_id=studio_id, peer_id=peer_id)
                messages = _chat_messages_for_store(chat, studio_id=studio_id)
                row = _match_row(
                    chat_name=chat_name,
                    phone=phone,
                    by_phone=by_phone,
                    by_name=by_name,
                    tg_nick=nick,
                    by_nick=by_nick,
                    text_phones=_phones_in_messages(messages),
                )
                if row is None:
                    continue
                client_id = str(row.get("_moysklad_id") or row.get("id") or "").strip()
                if not client_id:
                    continue
                matched += 1
                client_phone = dedupe_phone(row.get("Телефон") or row.get("phone")) or phone
                existing_nick = normalize_tg_nick(
                    row.get("ТГ ник") or row.get("tg_nick") or ""
                )
                use_nick = existing_nick or nick
                display_nick = ""
                if use_nick:
                    display_nick = (
                        f"@{use_nick}" if not str(use_nick).startswith("@") else use_nick
                    )
                if nick and not existing_nick:
                    nick_filled += 1
                    row["ТГ ник"] = display_nick
                    row["tg_nick"] = display_nick

                thread = _ensure_thread(
                    store,
                    client_id=client_id,
                    phone=client_phone,
                    tg_nick=use_nick,
                    client_name=str(row.get("Наименование") or chat_name),
                )
                existing = list(thread.get("messages") or [])
                kept = [m for m in existing if str(m.get("source") or "") != "telegram_export"]
                merged = kept + messages
                if len(merged) > _MAX_MESSAGES:
                    merged = merged[-_MAX_MESSAGES:]
                merged.sort(key=lambda m: str(m.get("ts") or ""))
                thread["messages"] = merged
                thread["tg_chat_id"] = str(peer_id or "")
                thread["updated_at"] = _now()
                imported_messages += len(messages)
                preview = preview_text(thread)

                if preview and not str(
                    row.get("TG conversation") or row.get("tg_conversation") or ""
                ).strip():
                    row["TG conversation"] = preview
                    row["tg_conversation"] = preview
                if peer_id:
                    row["tg_chat_id"] = str(peer_id)

                by_client[client_id] = {
                    "tg_chat_id": str(peer_id or ""),
                    "tg_nick": display_nick,
                    "phone": client_phone,
                    "chat_name": chat_name,
                    "message_count": len(messages),
                    "preview": preview,
                    "export_chat_id": str(chat.get("id") or ""),
                }

            _conv_save(store)

        stats = {
            "ok": True,
            "path": str(path),
            "matched": matched,
            "imported_messages": imported_messages,
            "nick_filled": nick_filled,
            "chats_total": sum(
                1 for c in chats if isinstance(c, dict) and c.get("type") == "personal_chat"
            ),
            "cache_backend": cache_backend_name(),
        }
        save_overlay({"by_client_id": by_client, "stats": stats})
        stamped = stamp_catalog_rows_from_overlay(rows)
        stats["stamped_rows"] = stamped
        _IMPORT_DONE_FOR.add(fp)
        log.info(
            "moysklad telegram export: matched=%s msgs=%s nicks=%s backend=%s path=%s",
            matched,
            imported_messages,
            nick_filled,
            cache_backend_name(),
            path,
        )
        return stats


def ensure_export_imported(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Lazy one-shot import when export file is present."""
    if resolve_export_path() is None:
        # Still apply cached overlay onto rows (Redis/file may have prior import).
        stamped = stamp_catalog_rows_from_overlay(rows)
        return {
            "ok": stamped > 0,
            "skipped": True,
            "error": "export_not_found" if stamped == 0 else None,
            "stamped_rows": stamped,
            "cache_backend": cache_backend_name(),
        }
    try:
        return import_export_into_catalog(rows)
    except Exception as exc:
        log.warning("moysklad telegram export import failed: %s", exc)
        return {"ok": False, "error": str(exc), "cache_backend": cache_backend_name()}


def apply_export_overlay_to_public(client: dict[str, Any]) -> dict[str, Any]:
    """Merge overlay tg_nick / tg_chat_id / preview into a public client dict."""
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
    preview = str(entry.get("preview") or "").strip()
    if preview and not str(out.get("tg_conversation") or "").strip():
        out["tg_conversation"] = preview
        out["tg_conversation_preview"] = preview
    if entry.get("message_count") is not None and not out.get("conversation_count"):
        out["conversation_count"] = int(entry.get("message_count") or 0)
    return out


def clear_import_memory_for_tests() -> None:
    global _OVERLAY_MEMORY, _OVERLAY_MEMORY_AT, _OVERLAY_MEMORY_FP
    _IMPORT_DONE_FOR.clear()
    with _CACHE_LOCK:
        _OVERLAY_MEMORY = None
        _OVERLAY_MEMORY_AT = 0.0
        _OVERLAY_MEMORY_FP = None
