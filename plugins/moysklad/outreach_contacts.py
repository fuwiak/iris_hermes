"""Outreach contact picker for MoySklad Рассылки.

Merges:
* custom contacts (user-added @nick / chat id) — ``outreach_contacts.json``
* Telegram Desktop export overlay peers (matched clients)
* catalog clients that already have ТГ ник / chat id
* the operator's own Telegram contact list (personal MTProto session)

Ids:
* catalog / overlay → MoySklad client id
* custom → ``custom:<uuid>``
* personal Telegram contacts → ``tg:<telegram user id>``
"""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any, Optional

from hermes_constants import get_hermes_home
from plugins.moysklad.conversations import normalize_tg_nick

_LOCK = threading.RLock()
_STORE_NAME = "outreach_contacts.json"


def _store_path() -> Path:
    root = get_hermes_home() / "moysklad"
    root.mkdir(parents=True, exist_ok=True)
    return root / _STORE_NAME


def load_custom_contacts() -> list[dict[str, Any]]:
    path = _store_path()
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = raw.get("contacts") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("id") or "").strip()
        if not cid.startswith("custom:"):
            continue
        out.append(_normalize_contact(item, source="custom"))
    return [c for c in out if c]


def save_custom_contacts(contacts: list[dict[str, Any]]) -> None:
    path = _store_path()
    payload = {
        "contacts": [
            {
                "id": c["id"],
                "name": c.get("name") or "",
                "tg_nick": c.get("tg_nick") or "",
                "tg_chat_id": c.get("tg_chat_id") or "",
            }
            for c in contacts
            if str(c.get("id") or "").startswith("custom:")
        ]
    }
    tmp = path.with_suffix(".tmp")
    with _LOCK:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)


def telegram_account_contacts(*, refresh: bool = False) -> list[dict[str, Any]]:
    """Contacts from the operator's personal Telegram session (cache-backed)."""
    try:
        from plugins.platforms.telegram_user import client as tg_user

        if refresh:
            res = tg_user.fetch_contacts(force=True)
            if res.get("ok"):
                return list(res.get("contacts") or [])
        return tg_user.cached_contacts()
    except Exception:
        return []


def _normalize_contact(raw: dict[str, Any], *, source: str) -> Optional[dict[str, Any]]:
    cid = str(raw.get("id") or "").strip()
    nick = normalize_tg_nick(raw.get("tg_nick") or "")
    chat_id = str(raw.get("tg_chat_id") or "").strip()
    name = str(raw.get("name") or "").strip()
    if not cid:
        return None
    if not nick and not chat_id:
        return None
    label_bits = [name] if name else []
    if nick:
        label_bits.append(f"@{nick}")
    elif chat_id:
        label_bits.append(chat_id)
    return {
        "id": cid,
        "name": name or (f"@{nick}" if nick else chat_id),
        "tg_nick": nick,
        "tg_chat_id": chat_id,
        "source": source,
        "label": " · ".join(label_bits) if label_bits else cid,
    }


def add_custom_contact(
    *,
    name: str = "",
    tg_nick: str = "",
    tg_chat_id: str = "",
    query: str = "",
    resolve: bool = True,
) -> dict[str, Any]:
    nick = normalize_tg_nick(tg_nick)
    chat_id = str(tg_chat_id or "").strip()
    raw_query = str(query or "").strip()

    if resolve and (nick or chat_id or raw_query):
        from plugins.moysklad.telegram_send import resolve_peer_identity

        resolved = resolve_peer_identity(
            tg_nick=nick, tg_chat_id=chat_id, query=raw_query
        )
        if not resolved.get("ok"):
            raise ValueError(resolved.get("detail") or resolved.get("error") or "resolve failed")
        nick = normalize_tg_nick(resolved.get("tg_nick") or nick)
        chat_id = str(resolved.get("tg_chat_id") or chat_id).strip()
        if not name:
            name = str(resolved.get("name") or "").strip()
        resolved_via = str(resolved.get("resolved_via") or "resolve")
    else:
        resolved_via = "manual"

    if not nick and not chat_id:
        raise ValueError("Укажите @ник, t.me/… или numeric chat id")

    contact = _normalize_contact(
        {
            "id": f"custom:{uuid.uuid4().hex[:12]}",
            "name": str(name or "").strip(),
            "tg_nick": nick,
            "tg_chat_id": chat_id,
        },
        source="custom",
    )
    assert contact is not None
    contact["resolved_via"] = resolved_via
    with _LOCK:
        items = load_custom_contacts()
        # de-dupe by nick or chat_id
        filtered = []
        for existing in items:
            same_nick = nick and existing.get("tg_nick") == nick
            same_chat = chat_id and existing.get("tg_chat_id") == chat_id
            if same_nick or same_chat:
                continue
            filtered.append(existing)
        filtered.append(contact)
        save_custom_contacts(filtered)
    return contact


def delete_custom_contact(contact_id: str) -> bool:
    cid = str(contact_id or "").strip()
    if not cid.startswith("custom:"):
        return False
    with _LOCK:
        items = load_custom_contacts()
        next_items = [c for c in items if c.get("id") != cid]
        if len(next_items) == len(items):
            return False
        save_custom_contacts(next_items)
    return True


def get_contact(contact_id: str) -> Optional[dict[str, Any]]:
    cid = str(contact_id or "").strip()
    if not cid:
        return None
    if cid.startswith("custom:"):
        for c in load_custom_contacts():
            if c.get("id") == cid:
                return c
        return None
    if cid.startswith("tg:"):
        key = cid.split(":", 1)[1].strip()
        try:
            from plugins.moysklad.telegram_archive import find_peer

            hit = find_peer(tg_chat_id=key if key.isdigit() else "", tg_nick="" if key.isdigit() else key)
            if hit:
                return _normalize_contact(
                    {
                        "id": cid,
                        "name": hit.get("name") or "",
                        "tg_nick": hit.get("tg_nick") or "",
                        "tg_chat_id": hit.get("tg_chat_id") or "",
                    },
                    source="tg_archive",
                )
        except Exception:
            pass
        for row in telegram_account_contacts():
            if str(row.get("tg_chat_id") or "") == key or row.get("tg_nick") == key:
                return _normalize_contact(
                    {
                        "id": cid,
                        "name": row.get("name") or "",
                        "tg_nick": row.get("tg_nick") or "",
                        "tg_chat_id": row.get("tg_chat_id") or "",
                    },
                    source="telegram",
                )
        # Cache miss (contact added after last sync) — the id still carries the peer.
        return _normalize_contact(
            {
                "id": cid,
                "name": "",
                "tg_nick": "" if key.isdigit() else key,
                "tg_chat_id": key if key.isdigit() else "",
            },
            source="telegram",
        )
    # overlay / will be resolved by caller with catalog
    try:
        from plugins.moysklad.telegram_export import overlay_for_client

        entry = overlay_for_client(cid)
        if entry:
            return _normalize_contact(
                {
                    "id": cid,
                    "name": entry.get("chat_name") or entry.get("name") or "",
                    "tg_nick": entry.get("tg_nick") or "",
                    "tg_chat_id": entry.get("tg_chat_id") or "",
                },
                source="export",
            )
    except Exception:
        pass
    return None


def list_outreach_contacts(
    *,
    catalog_clients: list[dict[str, Any]] | None = None,
    q: str = "",
    limit: int = 200,
    refresh: bool = False,
) -> list[dict[str, Any]]:
    """Merged contact list for the campaigns dropdown."""
    by_id: dict[str, dict[str, Any]] = {}

    for c in load_custom_contacts():
        by_id[c["id"]] = c

    try:
        from plugins.moysklad.telegram_export import load_overlay

        overlay = load_overlay()
        by_client = overlay.get("by_client_id") or {}
        if isinstance(by_client, dict):
            for cid, entry in by_client.items():
                if not isinstance(entry, dict):
                    continue
                norm = _normalize_contact(
                    {
                        "id": str(cid),
                        "name": entry.get("chat_name") or entry.get("name") or "",
                        "tg_nick": entry.get("tg_nick") or "",
                        "tg_chat_id": entry.get("tg_chat_id") or "",
                    },
                    source="export",
                )
                if norm and str(cid) not in by_id:
                    by_id[str(cid)] = norm
    except Exception:
        pass

    for row in catalog_clients or []:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("id") or "").strip()
        if not cid or cid in by_id:
            continue
        nick = normalize_tg_nick(row.get("tg_nick") or "")
        chat_id = str(row.get("tg_chat_id") or "").strip()
        if not nick and not chat_id:
            continue
        norm = _normalize_contact(
            {
                "id": cid,
                "name": row.get("name") or "",
                "tg_nick": nick,
                "tg_chat_id": chat_id,
            },
            source="catalog",
        )
        if norm:
            by_id[cid] = norm

    # Telegram export archive — peers with real history but no MoySklad card.
    # They carry a numeric chat id, so Business send can reach them.
    seen_chat = {str(c.get("tg_chat_id") or "") for c in by_id.values() if c.get("tg_chat_id")}
    seen_nick = {str(c.get("tg_nick") or "") for c in by_id.values() if c.get("tg_nick")}
    try:
        from plugins.moysklad.telegram_archive import archive_contacts

        for row in archive_contacts(unmatched_only=True):
            chat_id = str(row.get("tg_chat_id") or "").strip()
            nick = normalize_tg_nick(row.get("tg_nick") or "")
            if (chat_id and chat_id in seen_chat) or (nick and nick in seen_nick):
                continue
            if row["id"] in by_id:
                continue
            by_id[row["id"]] = row
            if chat_id:
                seen_chat.add(chat_id)
            if nick:
                seen_nick.add(nick)
    except Exception:
        pass

    # Personal Telegram contacts last — only peers the CRM doesn't already know.
    for row in telegram_account_contacts(refresh=refresh):
        chat_id = str(row.get("tg_chat_id") or "").strip()
        nick = normalize_tg_nick(row.get("tg_nick") or "")
        if (chat_id and chat_id in seen_chat) or (nick and nick in seen_nick):
            continue
        norm = _normalize_contact(
            {
                "id": f"tg:{chat_id or nick}",
                "name": row.get("name") or "",
                "tg_nick": nick,
                "tg_chat_id": chat_id,
            },
            source="telegram",
        )
        if norm and norm["id"] not in by_id:
            by_id[norm["id"]] = norm
            if chat_id:
                seen_chat.add(chat_id)
            if nick:
                seen_nick.add(nick)

    items = list(by_id.values())
    needle = (q or "").strip().lower().lstrip("@")
    if needle:
        items = [
            c
            for c in items
            if needle in (c.get("label") or "").lower()
            or needle in (c.get("name") or "").lower()
            or needle in (c.get("tg_nick") or "").lower()
            or needle in (c.get("tg_chat_id") or "").lower()
        ]
    items.sort(key=lambda c: (c.get("name") or c.get("label") or "").lower())
    return items[: max(1, min(int(limit or 200), 500))]
