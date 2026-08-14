"""История отправок — a flat feed of every outbound message.

The mass-send job store only remembers background blasts; single sends from
the client card, the dialog modal and «Отправить» on Рассылки live in the
conversations store. This scans that store so the operator sees «кому, что
и с каким статусом» in one list, newest first.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.moysklad.conversations import _LOCK, _load  # shared store access

log = logging.getLogger(__name__)

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
    """All outbound messages across threads, newest first."""
    try:
        cap = max(1, min(int(limit), 1000))
    except (TypeError, ValueError):
        cap = 200
    rows: list[dict[str, Any]] = []
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
