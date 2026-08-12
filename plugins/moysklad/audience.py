"""Mass-mailing audience filters over the deduped Clients catalog.

Used by ``/clients`` query extras and Рассылки filter builder. Counts always
come from rows that already passed multi-stage dedupe in ``classify``.
"""

from __future__ import annotations

import re
from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Any, Optional

from plugins.moysklad.dedupe import normalize_phone, normalize_telegram
from plugins.moysklad.groups import (
    normalize_group_key,
    row_ai_groups,
    row_all_groups,
    row_groups,
    row_has_group,
)
from plugins.moysklad.order_status import STAGE_FILTER_KEYS, client_stage
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
    idx = row.get("_group_index_v1") if isinstance(row, dict) else None
    if isinstance(idx, dict):
        # Stamped canonical keys — avoids per-click row_all_groups walks
        # (AI-fill store lookups per row made «ДР» take ~5s on 10k rows).
        keys = list((idx.get("ms") or {}).keys()) + list((idx.get("ai") or {}).keys())
        return any(
            _BIRTHDAY_RE.search(key) or "событие" in key for key in keys
        )
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


def parse_event_date(value: Any) -> Optional[date]:
    """Parse order/event timestamps into a calendar date.

    Accepts ISO ``YYYY-MM-DD``, MoySklad ``YYYY-MM-DD HH:MM:SS``,
    ``YYYY-MM-DDTHH:MM:SS``, and RU ``DD.MM.YYYY``.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text or text.lower() in ("none", "null"):
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y",
        "%d/%m/%Y",
    ):
        try:
            return datetime.strptime(text[:19], fmt).date()
        except ValueError:
            continue
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})", text)
    if m:
        dd, mm = int(m.group(1)), int(m.group(2))
        yy = int(m.group(3))
        if yy < 100:
            yy += 2000
        if 1 <= mm <= 12 and 1 <= dd <= 31:
            try:
                return date(yy, mm, dd)
            except ValueError:
                return None
    return None


def _collect_row_event_parts(
    row: dict[str, Any],
) -> tuple[list[date], list[tuple[int, int]]]:
    """Split event sources into exact days + annual (month, day) markers."""
    literals: list[date] = []
    annuals: list[tuple[int, int]] = []
    seen_lit: set[date] = set()
    seen_ann: set[tuple[int, int]] = set()

    def _lit(d: date) -> None:
        if d not in seen_lit:
            seen_lit.add(d)
            literals.append(d)

    def _ann(month: int, day: int) -> None:
        key = (month, day)
        if key not in seen_ann:
            seen_ann.add(key)
            annuals.append(key)

    for key in (
        "Дата рождения",
        "birthdate",
        "birthday",
        "День рождения",
    ):
        bd = _parse_birthdate(row.get(key))
        if bd:
            _ann(bd.month, bd.day)

    for group in row_all_groups(row):
        key = normalize_group_key(group)
        blob = key.lower().replace("ё", "е")
        if key in _FIXED_OCCASIONS:
            month, day = _FIXED_OCCASIONS[key]
            _ann(month, day)
            continue
        if _VALENTINE_RE.search(blob):
            _ann(2, 14)
            continue
        if _SEPTEMBER_RE.search(blob):
            _ann(9, 1)
            continue
        m = re.match(r"^событие\s+(.+)$", blob)
        if m:
            month = _MONTH_GENITIVE.get(m.group(1).strip())
            if month:
                _ann(month, 15)

    for item in row.get("_orders_context") or []:
        if not isinstance(item, dict):
            continue
        moment = (
            item.get("moment")
            or item.get("Дата")
            or item.get("date")
            or ""
        )
        order_day = parse_event_date(moment)
        if order_day is not None:
            _lit(order_day)
            _ann(order_day.month, order_day.day)
        month = item.get("_month")
        if month is None:
            moment_text = str(moment or "")
            if len(moment_text) >= 7 and moment_text[4] == "-":
                try:
                    month = int(moment_text[5:7])
                except ValueError:
                    month = None
            elif order_day is not None:
                month = order_day.month
        try:
            month_i = int(month) if month is not None else 0
        except (TypeError, ValueError):
            month_i = 0
        if month_i == 2:
            _ann(2, 14)
        elif month_i == 3:
            _ann(3, 8)
        elif month_i == 9:
            _ann(9, 1)
        elif month_i in (11, 12, 1):
            _ann(1, 1)
            if month_i == 11:
                _ann(11, 27)
        elif 1 <= month_i <= 12:
            _ann(month_i, 15)

    for key in ("last_order_at", "Дата последнего заказа"):
        last_day = parse_event_date(row.get(key))
        if last_day is not None:
            _lit(last_day)
            _ann(last_day.month, last_day.day)

    return literals, annuals


def stamp_row_event_index(row: dict[str, Any]) -> dict[str, Any]:
    """Persist cheap calendar index on the catalog row (mutates)."""
    if not isinstance(row, dict):
        return {"literals": [], "annuals": []}
    literals, annuals = _collect_row_event_parts(row)
    index = {
        "literals": [d.isoformat() for d in literals],
        "annuals": [[m, d] for m, d in annuals],
    }
    row["_event_index_v1"] = index
    return index


def event_dates_for_row(row: dict[str, Any], *, today: Optional[date] = None) -> list[date]:
    """Occasion + order dates for calendar / lead-window audience filters.

    Prefer stamped ``_event_index_v1`` (built once per catalog) so filter
    clicks stay O(markers) instead of re-parsing every order/tag.
    """
    today = today or date.today()
    found: list[date] = []
    seen: set[date] = set()

    def _add(d: date) -> None:
        if d not in seen:
            seen.add(d)
            found.append(d)

    idx = row.get("_event_index_v1") if isinstance(row, dict) else None
    if not isinstance(idx, dict):
        idx = stamp_row_event_index(row if isinstance(row, dict) else {})

    for iso in idx.get("literals") or []:
        order_day = parse_event_date(iso)
        if order_day is not None:
            _add(order_day)
            _add(_next_annual(order_day.month, order_day.day, today=today))

    for pair in idx.get("annuals") or []:
        try:
            month_i, day_i = int(pair[0]), int(pair[1])
        except (TypeError, ValueError, IndexError):
            continue
        if 1 <= month_i <= 12 and 1 <= day_i <= 31:
            _add(_next_annual(month_i, day_i, today=today))

    return found


def row_matches_event_calendar(
    row: dict[str, Any],
    *,
    event_from: Optional[date],
    event_to: Optional[date],
    lead_days: int = 0,
    today: Optional[date] = None,
) -> bool:
    """Filter by explicit event date(s) and optional contact lead window.

  - ``event_from`` / ``event_to``: inclusive calendar range for the event.
  - ``lead_days > 0``: today must fall in ``[event - lead, event]``.
  - ``lead_days == 0``: any event whose date lands in the selected range
    (past dates OK — seller is picking a calendar window, not «only future»).
    """
    if event_from is None and event_to is None:
        return True
    today = today or date.today()
    start = event_from or event_to
    end = event_to or event_from
    if start is None or end is None:
        return True
    if start > end:
        start, end = end, start
    try:
        lead = max(0, int(lead_days or 0))
    except (TypeError, ValueError):
        lead = 0
    # Resolve annual tags against today AND against the selected range so
    # picking «8 марта 2026» in August still matches the 8-March tag
    # (next-from-today alone would jump to 2027 and miss the range).
    dates: set[date] = set(event_dates_for_row(row, today=today))
    dates.update(event_dates_for_row(row, today=start))
    if end != start:
        dates.update(event_dates_for_row(row, today=end))
    if not dates:
        return False
    for event_date in dates:
        if not (start <= event_date <= end):
            continue
        if lead > 0:
            contact_start = event_date - timedelta(days=lead)
            if contact_start <= today <= event_date:
                return True
        else:
            return True
    return False


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


def normalize_stage_filter(value: Any) -> str:
    """``all`` | ``failed`` | ``customer`` | ``no_orders`` | ``unknown``."""
    key = str(value or "").strip().lower().replace("ё", "е")
    if not key or key in ("all", "any", "все"):
        return "all"
    if key in ("failed", "не состоялся", "не состоялись", "несостоявшиеся"):
        return "failed"
    if key in ("customer", "покупатель", "покупатели"):
        return "customer"
    if key in ("no_orders", "нет заказов", "без заказов"):
        return "no_orders"
    if key in ("unknown", "нет данных"):
        return "unknown"
    return "all"


def row_client_stage(row: dict[str, Any]) -> str:
    """Stage label for a catalog row, recomputed when the cache predates it."""
    stage = str(row.get("client_stage") or row.get("Тип клиента") or "").strip()
    if stage:
        return stage
    return client_stage(row.get("customer_outcome") or "none")


def row_matches_stage(row: dict[str, Any], stage_filter: Any) -> bool:
    key = normalize_stage_filter(stage_filter)
    if key == "all":
        return True
    return row_client_stage(row) == STAGE_FILTER_KEYS.get(key, "")


def stage_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    """How many rows sit in each stage — drives the filter chip labels."""
    counts = {key: 0 for key in STAGE_FILTER_KEYS}
    for row in rows:
        if not isinstance(row, dict):
            continue
        stage = row_client_stage(row)
        for key, label in STAGE_FILTER_KEYS.items():
            if stage == label:
                counts[key] += 1
                break
    counts["all"] = len([r for r in rows if isinstance(r, dict)])
    return counts


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
    event_date_from: str = "",
    event_date_to: str = "",
    stage: str = "all",
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
    if not row_matches_stage(row, stage):
        return False
    try:
        window = int(days_before_event or 0)
    except (TypeError, ValueError):
        window = 0
    ef = parse_event_date(event_date_from)
    et = parse_event_date(event_date_to)
    if ef or et:
        if not row_matches_event_calendar(
            row,
            event_from=ef,
            event_to=et,
            lead_days=window,
        ):
            return False
    elif window > 0:
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
