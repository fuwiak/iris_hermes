"""Mass-mailing audience filters over the deduped Clients catalog.

Used by ``/clients`` query extras and Рассылки filter builder. Counts always
come from rows that already passed multi-stage dedupe in ``classify``.
"""

from __future__ import annotations

import re
from calendar import monthrange
from datetime import date, datetime
from typing import Any, Optional

from plugins.moysklad.dedupe import normalize_phone, normalize_telegram
from plugins.moysklad.groups import (
    normalize_group_key,
    row_ai_groups,
    row_all_groups,
    row_groups,
    row_has_group,
)
from plugins.moysklad.sales_channels import unique_sales_channels

# Keep VIP regex local — avoid classify ↔ client_card import cycles.
_VIP_RE = re.compile(r"\b(vip|вип)\b", re.IGNORECASE)
_BIRTHDAY_RE = re.compile(
    r"(день\s*рожден|др\b|birthday|событие)",
    re.IGNORECASE,
)

# Genitive month → month number (matches groups.EVENT_MONTH_GROUPS).
_MONTH_GENITIVE: dict[str, int] = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}

# Fixed annual occasions → (month, day).
_FIXED_OCCASIONS: dict[str, tuple[int, int]] = {
    "8 марта": (3, 8),
    "новый год": (1, 1),
    "день мам": (11, 27),  # approximate RU Mother's Day (last Sunday Nov)
    "день матери": (11, 27),
}

_VALENTINE_RE = re.compile(r"валентин|14\s*феврал", re.I)
_SEPTEMBER_RE = re.compile(r"1\s*сентябр|знан", re.I)


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
    for group in row_all_groups(row):
        if _BIRTHDAY_RE.search(group):
            return True
        if "событие" in group.lower().replace("ё", "е"):
            return True
    return False


def _next_annual(month: int, day: int, *, today: date) -> date:
    """Next occurrence of month/day on or after today (clamp day to month length)."""
    last = monthrange(today.year, month)[1]
    d = min(day, last)
    candidate = date(today.year, month, d)
    if candidate < today:
        last_next = monthrange(today.year + 1, month)[1]
        candidate = date(today.year + 1, month, min(day, last_next))
    return candidate


def _parse_birthdate(raw: Any) -> Optional[date]:
    text = str(raw or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt).date()
        except ValueError:
            continue
    m = re.match(r"^(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?$", text)
    if m:
        dd, mm = int(m.group(1)), int(m.group(2))
        if 1 <= mm <= 12 and 1 <= dd <= 31:
            try:
                year = int(m.group(3)) if m.group(3) else date.today().year
                if year < 100:
                    year += 2000
                return date(year, mm, dd)
            except ValueError:
                return None
    return None


def event_dates_for_row(row: dict[str, Any], *, today: Optional[date] = None) -> list[date]:
    """Upcoming occasion dates derived from groups/tags/birthdate attrs."""
    today = today or date.today()
    found: list[date] = []
    seen: set[date] = set()

    def _add(d: date) -> None:
        if d not in seen:
            seen.add(d)
            found.append(d)

    # Explicit birthdate attributes from MoySklad / AI fill.
    for key in (
        "Дата рождения",
        "birthdate",
        "birthday",
        "День рождения",
    ):
        bd = _parse_birthdate(row.get(key))
        if bd:
            _add(_next_annual(bd.month, bd.day, today=today))

    for group in row_all_groups(row):
        key = normalize_group_key(group)
        blob = key.lower().replace("ё", "е")
        if key in _FIXED_OCCASIONS:
            month, day = _FIXED_OCCASIONS[key]
            _add(_next_annual(month, day, today=today))
            continue
        if _VALENTINE_RE.search(blob):
            _add(_next_annual(2, 14, today=today))
            continue
        if _SEPTEMBER_RE.search(blob):
            _add(_next_annual(9, 1, today=today))
            continue
        m = re.match(r"^событие\s+(.+)$", blob)
        if m:
            month = _MONTH_GENITIVE.get(m.group(1).strip())
            if month:
                # Mid-month default for «событие марта» buckets.
                _add(_next_annual(month, 15, today=today))

    # Soft occasions from paid/any order months — so «N дней до события»
    # finds clients with seasonal history even without explicit tags.
    for item in row.get("_orders_context") or []:
        if not isinstance(item, dict):
            continue
        month = item.get("_month")
        if month is None:
            moment = str(item.get("moment") or item.get("Дата") or "")
            if len(moment) >= 7 and moment[4] == "-":
                try:
                    month = int(moment[5:7])
                except ValueError:
                    month = None
        try:
            month_i = int(month) if month is not None else 0
        except (TypeError, ValueError):
            month_i = 0
        if month_i == 2:
            _add(_next_annual(2, 14, today=today))
        elif month_i == 3:
            _add(_next_annual(3, 8, today=today))
        elif month_i == 9:
            _add(_next_annual(9, 1, today=today))
        elif month_i in (11, 12, 1):
            _add(_next_annual(1, 1, today=today))
            if month_i == 11:
                _add(_next_annual(11, 27, today=today))
    return found


def row_matches_days_before_event(
    row: dict[str, Any],
    days: int,
    *,
    today: Optional[date] = None,
) -> bool:
    """True when an upcoming event falls within ``days`` days from today.

    ``days <= 0`` disables the window filter (always True).
    Window is inclusive: delta in ``[0, days]``.

    If no concrete dates resolve, fall back to soft birthday/occasion tags so
    the chip does not empty the whole audience list.
    """
    try:
        window = int(days)
    except (TypeError, ValueError):
        window = 0
    if window <= 0:
        return True
    today = today or date.today()
    dates = event_dates_for_row(row, today=today)
    if dates:
        for event_date in dates:
            delta = (event_date - today).days
            if 0 <= delta <= window:
                return True
        return False
    # No dated events — keep tagged «событие» / ДР clients visible.
    return row_matches_birthday_occasion(row)


def normalize_group_source(value: str) -> str:
    """``any`` | ``ms`` | ``ai``."""
    key = (value or "").strip().lower().replace("ё", "е")
    if key in ("ms", "moysklad", "мойсклад", "мск", "mcs"):
        return "ms"
    if key in ("ai", "ии", "llm", "heuristic"):
        return "ai"
    return "any"


def row_matches_audience_extras(
    row: dict[str, Any],
    *,
    channel_kind: str = "",
    require_phone: bool = False,
    require_telegram: bool = False,
    vip_only: bool = False,
    birthday_soon: bool = False,
    group: str = "",
    group_source: str = "any",
    days_before_event: int = 0,
) -> bool:
    source = normalize_group_source(group_source)
    if group and not row_has_group(row, group, source=source):
        return False
    # When filtering by source without a specific group chip, still require
    # that the row has at least one group from that source.
    if not group and source == "ms" and not row_groups(row):
        return False
    if not group and source == "ai" and not row_ai_groups(row):
        return False
    if not row_matches_channel_kind(row, channel_kind):
        return False
    if require_phone and not row_has_phone(row):
        return False
    if require_telegram and not row_has_telegram(row):
        return False
    if vip_only and not row_is_vip(row):
        return False
    try:
        window = int(days_before_event or 0)
    except (TypeError, ValueError):
        window = 0
    if window > 0:
        if not row_matches_days_before_event(row, window):
            return False
    elif birthday_soon and not row_matches_birthday_occasion(row):
        return False
    return True


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in ("1", "true", "yes", "on", "да")
