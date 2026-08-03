"""CRM sales-channel rules (ported from client_segmentation_deepseek/app/services/fields.py).

Tabs «Маркетплейс» / «Прямые» use audience matchers — not naive «not direct».
"""

from __future__ import annotations

import re
from typing import Any

DIRECT_SALES_CHANNEL_EXACT = frozenset({
    "telegram",
    "whatsapp/max",
    "whatsapp",
    "watsapp",
    "max",
    "витрина",
    "прямые продажи",
    "сайт vereskflowers.ru",
    "сайт",
})

DIRECT_SALES_CHANNEL_SUBSTRINGS = (
    "vereskflowers.ru",
    "telegram",
    "whatsapp",
    "watsapp",
    "вотсап",
    "ватсап",
    "телеграм",
)

DIRECT_AUDIENCE_CHANNELS = (
    "прямые продажи",
    "вотсап/макс",
    "вотсап",
    "ватсап",
    "whatsapp/max",
    "whatsapp",
    "watsapp",
    "max",
    "макс",
    "телеграмм",
    "телеграм",
    "telegram",
    "сайт",
    "витрина",
    "vereskflowers",
)

MARKETPLACE_AUDIENCE_CHANNELS = (
    "flowwow floday",
    "flowwowfloday",
    "flowwow сокольники",
    "flowwowсокольники",
    "flowwow университет",
    "flowwowуниверситет",
    "flow wow floday",
    "floday",
    "флау вау",
    "флаувай",
)

MARKETPLACE_AUDIENCE_STATUSES = (
    "постоянный маркетплейсы",
    "постоянный маркетплейс",
    "новый",
)

MARKETPLACE_AUDIENCE_GROUPS = (
    "8 марта",
    "день мам",
    "день матери",
    "букет от 10000",
    "букет от 10 000",
    "новый год",
    "цветы для интерьера",
    "флау вау",
    "флау вай скайлофт",
    "флаувай скайлофт",
    "скайлофт",
)

MARKETPLACE_AUDIENCE_GROUP_PATTERNS = (
    r"событи",
    r"flow\s*wow",
    r"флау",
)

SALES_CHANNEL_TYPE_MARKETPLACE = "маркетплейс"
SALES_CHANNEL_TYPE_DIRECT = "прямые продажи"


def _normalize_channel(channel: str) -> str:
    return channel.strip().lower().replace("ё", "е")


def is_direct_sales_channel(channel: str | None) -> bool:
    if not channel or not str(channel).strip():
        return False
    text = _normalize_channel(str(channel))
    if text in DIRECT_SALES_CHANNEL_EXACT:
        return True
    if text == "сайт" or text.startswith("сайт "):
        return True
    return any(part in text for part in DIRECT_SALES_CHANNEL_SUBSTRINGS)


def is_marketplace_channel(channel: str | None) -> bool:
    if not channel or not str(channel).strip():
        return False
    return not is_direct_sales_channel(channel)


def _norm_token(value: str) -> str:
    return (
        str(value or "")
        .strip()
        .lower()
        .replace("ё", "е")
        .replace("—", " ")
        .replace("-", " ")
    )


def _token_matches_any(value: str, needles: tuple[str, ...] | list[str]) -> bool:
    text = _norm_token(value)
    if not text:
        return False
    compact = re.sub(r"\s+", " ", text)
    compact_nospace = re.sub(r"\s+", "", compact)
    for needle in needles:
        n = _norm_token(needle)
        if not n:
            continue
        n_space = re.sub(r"\s+", " ", n)
        n_nospace = re.sub(r"\s+", "", n_space)
        if (
            compact == n_space
            or n_space in compact
            or compact_nospace == n_nospace
            or n_nospace in compact_nospace
        ):
            return True
    return False


def _token_matches_patterns(value: str, patterns: tuple[str, ...] | list[str]) -> bool:
    text = _norm_token(value)
    if not text:
        return False
    return any(re.search(pat, text, flags=re.IGNORECASE) for pat in patterns)


def _status_matches_allowlist(value: str, needles: tuple[str, ...] | list[str]) -> bool:
    text = re.sub(r"\s+", " ", _norm_token(value))
    if not text or text in {"без статуса", "безстатуса"}:
        return False
    for needle in needles:
        n = re.sub(r"\s+", " ", _norm_token(needle))
        if not n:
            continue
        if text == n:
            return True
        if len(n) < 6:
            continue
        if text.startswith(n + " "):
            return True
    return False


def moysklad_group_tokens(row: dict[str, Any]) -> list[str]:
    raw = str(
        row.get("_moysklad_tags_display")
        or " ".join(str(t) for t in (row.get("_moysklad_tags") or []))
        or row.get("Группы")
        or ""
    ).strip()
    if not raw:
        return []
    parts = re.split(r"[,/|;]", raw)
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        name = part.strip()
        key = name.lower()
        if name and key not in seen:
            seen.add(key)
            out.append(name)
    return out


def moysklad_status_tokens(row: dict[str, Any]) -> list[str]:
    status = str(row.get("Статус контрагента") or row.get("_moysklad_state") or "").strip()
    return [status] if status else []


def unique_sales_channels(row: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for order in row.get("_orders_context") or []:
        ch = order.get("Канал продаж")
        if ch and str(ch).strip():
            key = str(ch).strip().lower()
            if key not in seen:
                seen.add(key)
                result.append(str(ch).strip())
    stored = str(row.get("Канал продаж") or "").strip()
    if stored:
        key = stored.lower()
        if key not in seen:
            result.append(stored)
    for tag in moysklad_group_tokens(row):
        if is_direct_sales_channel(tag) or is_marketplace_channel(tag):
            key = tag.lower()
            if key not in seen:
                seen.add(key)
                result.append(tag)
    return result


def _row_matches_audience(
    row: dict[str, Any],
    *,
    channels: tuple[str, ...],
    statuses: tuple[str, ...],
    groups: tuple[str, ...],
    group_patterns: tuple[str, ...] = (),
) -> bool:
    for ch in unique_sales_channels(row):
        if _token_matches_any(ch, channels):
            return True
    stored_channel = str(row.get("Канал продаж") or "")
    if stored_channel and _token_matches_any(stored_channel, channels):
        return True
    for status in moysklad_status_tokens(row):
        if _status_matches_allowlist(status, statuses):
            return True
    for group in moysklad_group_tokens(row):
        if (
            _token_matches_any(group, groups)
            or _token_matches_patterns(group, group_patterns)
            or _token_matches_any(group, channels)
        ):
            return True
    return False


def row_matches_direct_audience(row: dict[str, Any]) -> bool:
    """CRM tab «Прямые»: only pure direct channels (any MP channel excludes)."""
    order_channels = unique_sales_channels(row)
    if any(is_marketplace_channel(c) for c in order_channels):
        return False
    return _row_matches_audience(
        row,
        channels=DIRECT_AUDIENCE_CHANNELS,
        statuses=(),
        groups=(),
        group_patterns=(),
    )


def row_matches_marketplace_audience(row: dict[str, Any]) -> bool:
    """CRM tab «Маркетплейс»: FlowWow channels ∪ statuses ∪ groups."""
    return _row_matches_audience(
        row,
        channels=MARKETPLACE_AUDIENCE_CHANNELS,
        statuses=MARKETPLACE_AUDIENCE_STATUSES,
        groups=MARKETPLACE_AUDIENCE_GROUPS,
        group_patterns=MARKETPLACE_AUDIENCE_GROUP_PATTERNS,
    )


def sales_channel_type_from_channels(channels: list[str]) -> str:
    if not channels:
        return SALES_CHANNEL_TYPE_DIRECT
    has_direct = False
    has_marketplace = False
    for channel in channels:
        if not channel or not str(channel).strip():
            continue
        if is_direct_sales_channel(channel):
            has_direct = True
        else:
            has_marketplace = True
    if has_marketplace:
        return SALES_CHANNEL_TYPE_MARKETPLACE
    if has_direct:
        return SALES_CHANNEL_TYPE_DIRECT
    return SALES_CHANNEL_TYPE_DIRECT


def row_matches_sales_filter(row: dict[str, Any], sales_filter: str) -> bool:
    key = (sales_filter or "all").strip().lower()
    if key in ("", "all"):
        return True
    if key in ("marketplace", "маркетплейс", "mp"):
        return row_matches_marketplace_audience(row)
    if key in ("direct", "прямые", "прямые продажи"):
        return row_matches_direct_audience(row)
    return True


def entity_ref_id(ref: Any) -> str | None:
    if isinstance(ref, dict):
        rid = ref.get("id")
        if rid:
            return str(rid)
        meta = ref.get("meta") or {}
        href = str(meta.get("href") or "")
        if href:
            return href.rstrip("/").rsplit("/", 1)[-1] or None
    return None


def channel_name_from_order(
    order: dict[str, Any], channels_by_id: dict[str, str] | None = None
) -> str | None:
    sc = order.get("salesChannel") or order.get("sales_channel")
    if isinstance(sc, str) and sc.strip():
        return sc.strip()
    if isinstance(sc, dict):
        name = sc.get("name")
        if name:
            return str(name).strip()
        channel_id = entity_ref_id(sc)
        if channel_id and channels_by_id:
            label = channels_by_id.get(channel_id)
            if label:
                return label
    return None


def sales_channels_by_id(channels: list[dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for channel in channels:
        channel_id = str(channel.get("id") or "").strip()
        name = str(channel.get("name") or "").strip()
        if channel_id and name:
            result[channel_id] = name
    return result


def counterparty_row_from_api(
    cp: dict[str, Any],
    *,
    order_channels: list[str] | None = None,
) -> dict[str, Any]:
    state = cp.get("state")
    state_name = ""
    if isinstance(state, dict):
        state_name = str(state.get("name") or "").strip()
    elif isinstance(state, str):
        state_name = state.strip()
    tags = cp.get("tags") if isinstance(cp.get("tags"), list) else []
    channels = [c for c in (order_channels or []) if c]
    return {
        "_moysklad_id": str(cp.get("id") or ""),
        "Наименование": str(cp.get("name") or "").strip(),
        "Телефон": str(cp.get("phone") or "").strip(),
        "Группы": ", ".join(str(t) for t in tags if str(t).strip()),
        "_moysklad_tags": list(tags),
        "_moysklad_tags_display": ", ".join(str(t) for t in tags if str(t).strip()),
        "_moysklad_state": state_name,
        "Статус контрагента": state_name,
        "_orders_context": [{"Канал продаж": c} for c in channels],
        "_order_channels_all": channels,
        "Канал продаж": channels[0] if channels else "",
        "Тип продаж": sales_channel_type_from_channels(channels),
    }
