"""Mass-mailing audience filters over the deduped Clients catalog.

Used by ``/clients`` query extras and Рассылки filter builder. Counts always
come from rows that already passed multi-stage dedupe in ``classify``.
"""

from __future__ import annotations

import re
from typing import Any

from plugins.moysklad.dedupe import normalize_phone, normalize_telegram
from plugins.moysklad.groups import row_has_group, row_groups
from plugins.moysklad.sales_channels import unique_sales_channels

# Keep VIP regex local — avoid classify ↔ client_card import cycles.
_VIP_RE = re.compile(r"\b(vip|вип)\b", re.IGNORECASE)
_BIRTHDAY_RE = re.compile(
    r"(день\s*рожден|др\b|birthday|событие)",
    re.IGNORECASE,
)


def row_has_phone(row: dict[str, Any]) -> bool:
    return bool(
        normalize_phone(row.get("Телефон") or row.get("phone"))
    )


def row_has_telegram(row: dict[str, Any]) -> bool:
    return bool(
        normalize_telegram(
            row.get("ТГ ник")
            or row.get("tg_nick")
            or row.get("TG conversation")
            or row.get("tg_conversation")
        )
    )


def row_is_vip(row: dict[str, Any]) -> bool:
    tags = list(row.get("_moysklad_tags") or row.get("tags") or [])
    state = str(
        row.get("_moysklad_state")
        or row.get("Статус")
        or row.get("state")
        or ""
    )
    blob = " ".join(str(t) for t in tags) + " " + state
    return bool(_VIP_RE.search(blob))


def row_matches_channel_kind(row: dict[str, Any], channel_kind: str) -> bool:
    """``telegram`` / ``whatsapp`` / empty(=any)."""
    key = (channel_kind or "").strip().lower()
    if key in ("", "any", "all"):
        return True
    channels = [c.lower() for c in unique_sales_channels(row)]
    blob = " ".join(channels)
    if key in ("telegram", "tg"):
        if row_has_telegram(row):
            return True
        return "telegram" in blob or "телеграм" in blob
    if key in ("whatsapp", "wa", "max"):
        # WhatsApp delivery needs a phone; also accept WA-labeled channels.
        if row_has_phone(row):
            return True
        return any(
            part in blob
            for part in ("whatsapp", "watsapp", "вотсап", "ватсап", "max", "макс")
        )
    return True


def row_matches_birthday_occasion(row: dict[str, Any]) -> bool:
    """Tags / groups hinting birthday or generic «событие» month buckets."""
    for group in row_groups(row):
        if _BIRTHDAY_RE.search(group):
            return True
        if "событие" in group.lower().replace("ё", "е"):
            return True
    return False


def row_matches_audience_extras(
    row: dict[str, Any],
    *,
    channel_kind: str = "",
    require_phone: bool = False,
    require_telegram: bool = False,
    vip_only: bool = False,
    birthday_soon: bool = False,
    group: str = "",
) -> bool:
    if group and not row_has_group(row, group):
        return False
    if not row_matches_channel_kind(row, channel_kind):
        return False
    if require_phone and not row_has_phone(row):
        return False
    if require_telegram and not row_has_telegram(row):
        return False
    if vip_only and not row_is_vip(row):
        return False
    if birthday_soon and not row_matches_birthday_occasion(row):
        return False
    return True


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in ("1", "true", "yes", "on", "да")
