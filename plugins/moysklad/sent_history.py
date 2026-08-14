"""История отправок — a flat feed of every outbound message.

The mass-send job store only remembers background blasts; single sends from
the client card, the dialog modal and «Отправить» on Рассылки live in the
conversations store. This scans that store so the operator sees «кому, что
и с каким статусом» in one list, newest first.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home
from plugins.moysklad.conversations import _LOCK, _load  # shared store access

log = logging.getLogger(__name__)

# Durable append-only log: the conversations store trims threads to 200
# messages and expires in 30 days, so «кому что отправляли» would silently
# forget old sends. The jsonl keeps every outbound forever (tiny rows).
_LOG_LOCK = threading.Lock()
_LOG_MAX_READ_BYTES = 4 * 1024 * 1024  # read at most the last ~4MB


def _log_path() -> Path:
    root = get_hermes_home() / "moysklad"
    root.mkdir(parents=True, exist_ok=True)
    return root / "sent_log.jsonl"


def record_sent(entry: dict[str, Any]) -> None:
    """Append one outbound send to the durable log (best-effort)."""
    try:
        row = {
            "client_id": str(entry.get("client_id") or ""),
            "client_name": str(entry.get("client_name") or ""),
            "tg_nick": str(entry.get("tg_nick") or ""),
            "text": str(entry.get("text") or "")[:500],
            "ts": str(entry.get("ts") or ""),
            "channel": str(entry.get("channel") or "telegram"),
            "source": str(entry.get("source") or ""),
        }
        with _LOG_LOCK:
            with _log_path().open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        log.debug("sent log append failed", exc_info=True)


def _read_log_rows() -> list[dict[str, Any]]:
    path = _log_path()
    if not path.is_file():
        return []
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > _LOG_MAX_READ_BYTES:
                fh.seek(size - _LOG_MAX_READ_BYTES)
                fh.readline()  # drop the partial first line
            blob = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in blob.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and str(row.get("text") or "").strip():
            rows.append(row)
    return rows

# Outbound sources that were confirmed delivered by the send path itself
# (Bot API ok) or that came back from real Telegram history — a message
# present in the chat history was, by definition, delivered.
_DELIVERED_SOURCES = frozenset(
    {
        "campaign_telegram_bot",
        "client_card_telegram_bot",
        "telegram_user",
        "telegram_user_history",
        "gateway_telegram",
        "telegram_export",
    }
)

# Never show store-seeding noise as «отправлено».
_SKIP_SOURCES = frozenset({"moysklad_attr"})


def delivery_status(source: str) -> str:
    src = (source or "").strip().lower()
    if src in _DELIVERED_SOURCES:
        return "delivered"
    return "recorded"


def list_sent_messages(*, limit: int = 200) -> list[dict[str, Any]]:
    """All outbound messages, newest first: durable log ∪ conversations."""
    try:
        cap = max(1, min(int(limit), 1000))
    except (TypeError, ValueError):
        cap = 200
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in _read_log_rows():
        source = str(row.get("source") or "")
        if source in _SKIP_SOURCES:
            continue
        key = (
            str(row.get("client_id") or ""),
            str(row.get("ts") or "")[:19],
            str(row.get("text") or "")[:80],
        )
        if key in seen:
            continue
        seen.add(key)
        rows.append({**row, "status": delivery_status(source)})
    with _LOCK:
        store = _load()
        threads = store.get("threads") or {}
        for tid, thread in threads.items():
            if not isinstance(thread, dict):
                continue
            client_id = str(thread.get("client_id") or tid or "")
            client_name = str(thread.get("client_name") or "")
            tg_nick = str(thread.get("tg_nick") or "")
            for msg in thread.get("messages") or []:
                if not isinstance(msg, dict):
                    continue
                if str(msg.get("direction") or "") != "outbound":
                    continue
                source = str(msg.get("source") or "")
                if source in _SKIP_SOURCES:
                    continue
                text = str(msg.get("text") or "").strip()
                if not text:
                    continue
                key = (
                    client_id,
                    str(msg.get("ts") or "")[:19],
                    text[:80],
                )
                if key in seen:
                    continue
                seen.add(key)
                rows.append(
                    {
                        "client_id": client_id,
                        "client_name": client_name,
                        "tg_nick": tg_nick,
                        "text": text[:500],
                        "ts": str(msg.get("ts") or ""),
                        "channel": str(msg.get("channel") or "telegram"),
                        "source": source,
                        "status": delivery_status(source),
                    }
                )
    rows.sort(key=lambda r: r["ts"], reverse=True)
    return rows[:cap]
