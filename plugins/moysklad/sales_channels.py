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
SALES_CHANNEL_TYPE_HYBRID = "маркетплейс/прямые продажи"


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


def _looks_like_sales_type_label(value: str) -> bool:
    text = _normalize_channel(value)
    if text in {
        SALES_CHANNEL_TYPE_DIRECT,
        SALES_CHANNEL_TYPE_MARKETPLACE,
        SALES_CHANNEL_TYPE_HYBRID,
        "прямые",
        "маркетплейсы",
        "marketplace",
        "direct",
        "hybrid",
    }:
        return True
    return "прямы" in text and "маркет" in text


def sales_channel_type_from_channels(channels: list[str]) -> str:
    """Classify channel set: direct | marketplace | hybrid (both).

    Hybrid when the client has ≥1 direct order channel AND ≥1 marketplace
    order channel — display label ``маркетплейс/прямые продажи``.
    """
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
    if has_marketplace and has_direct:
        return SALES_CHANNEL_TYPE_HYBRID
    if has_marketplace:
        return SALES_CHANNEL_TYPE_MARKETPLACE
    if has_direct:
        return SALES_CHANNEL_TYPE_DIRECT
    return SALES_CHANNEL_TYPE_DIRECT


def unique_sales_channels(row: dict[str, Any]) -> list[str]:
    """Unique sales channels from orders / row field — never from group tags.

    Tags like «8 марта» are groups, not channels. Treating every non-direct tag as
    a marketplace channel wrongly emptied the «Прямые» tab.
    """
    seen: set[str] = set()
    result: list[str] = []

    def _add(raw: Any) -> None:
        if raw is None:
            return
        text = str(raw).strip()
        if not text or _looks_like_sales_type_label(text):
            return
        for part in text.split(","):
            ch = part.strip()
            if not ch or _looks_like_sales_type_label(ch):
                continue
            key = ch.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(ch)

    stored_all = row.get("_order_channels_all")
    for order in row.get("_orders_context") or []:
        if isinstance(order, dict):
            _add(order.get("Канал продаж") or order.get("channel"))
    if isinstance(stored_all, list):
        for ch in stored_all:
            _add(ch)
    # Prefer order-derived list; fall back to stored field only when empty,
    # and never treat a comma-joined type label as a channel.
    if not result:
        _add(row.get("Канал продаж"))
    # Channel names sometimes live only in MoySklad tags (витрина / watsapp / флау вау).
    for tag in moysklad_group_tokens(row):
        if _token_matches_any(tag, DIRECT_AUDIENCE_CHANNELS) or _token_matches_any(
            tag, MARKETPLACE_AUDIENCE_CHANNELS
        ):
            _add(tag)
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


def _row_has_marketplace_order_channel(row: dict[str, Any]) -> bool:
    return any(is_marketplace_channel(c) for c in unique_sales_channels(row))


def _row_matches_marketplace_markers(row: dict[str, Any]) -> bool:
    """FlowWow allowlist ∪ marketplace statuses ∪ occasion groups (TZ)."""
    return _row_matches_audience(
        row,
        channels=MARKETPLACE_AUDIENCE_CHANNELS,
        statuses=MARKETPLACE_AUDIENCE_STATUSES,
        groups=MARKETPLACE_AUDIENCE_GROUPS,
        group_patterns=MARKETPLACE_AUDIENCE_GROUP_PATTERNS,
    )


def row_audience_bucket(row: dict[str, Any]) -> str:
    """Exclusive CRM tab: ``marketplace`` | ``direct``.

    Invariant: every client is in exactly one bucket, so
    ``direct + marketplace == total`` (no «other» gap, no double-count).

    Marketplace wins when any non-direct order channel exists, or when
    marketplace status/group/FlowWow markers match. Everyone else → direct
    (including clients with no channel yet). Hybrid (both channel types)
    lands in marketplace.
    """
    if _row_has_marketplace_order_channel(row):
        return "marketplace"
    if _row_matches_marketplace_markers(row):
        return "marketplace"
    return "direct"


def row_matches_direct_audience(row: dict[str, Any]) -> bool:
    """CRM tab «Прямые»: exclusive complement of marketplace."""
    return row_audience_bucket(row) == "direct"


def row_matches_marketplace_audience(row: dict[str, Any]) -> bool:
    """CRM tab «Маркетплейс»: order MP channels ∪ FlowWow/status/group markers."""
    return row_audience_bucket(row) == "marketplace"


def refresh_row_channel_fields(row: dict[str, Any]) -> dict[str, Any]:
    """Rewrite channel list + sales type from order context (cache-safe)."""
    channels = unique_sales_channels(row)
    row["_order_channels_all"] = list(channels)
    row["Канал продаж"] = ", ".join(channels)
    label = sales_channel_type_from_channels(channels)
    row["Тип канала продаж"] = label
    row["Тип продаж"] = label
    return row


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


_COMPANY_TYPE_LABELS = {
    "legal": "Юридическое лицо",
    "entrepreneur": "Индивидуальный предприниматель",
    "individual": "Физическое лицо",
}

_SEX_LABELS = {
    "male": "Мужской",
    "female": "Женский",
    "м": "Мужской",
    "ж": "Женский",
    "мужской": "Мужской",
    "женский": "Женский",
}

# Custom MoySklad attribute names → CRM column keys (case-insensitive match).
_ATTR_ALIASES: dict[str, tuple[str, ...]] = {
    "bonus_points": (
        "баллы начисленные",
        "баллы",
        "начисленные баллы",
        "бонусы",
        "bonus",
        "bonus points",
    ),
    "role": (
        "заказчик или получатель",
        "заказчик/получатель",
        "заказчик либо получатель",
        "роль",
        "тип клиента",
        "заказчик",
        "получатель",
    ),
    "actual_address_comment": (
        "фактический адрес (комментарий)",
        "фактический адрес комментарий",
        "комментарий к адресу",
        "адрес комментарий",
    ),
    "tg_nick": (
        "тг ник",
        "тг никнейм",
        "тг username",
        "telegram nick",
        "telegram nickname",
        "telegram username",
        "telegram",
        "телеграм",
        "username telegram",
        "ник телеграм",
        "tg",
        "тг",
    ),
    "tg_conversation": (
        "tg conversation",
        "telegram conversation",
        "tg chat",
        "telegram chat",
        "тг диалог",
        "телеграм диалог",
    ),
    "sex": ("пол", "sex", "gender"),
}


def _attr_value_as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    if isinstance(value, dict):
        for key in ("name", "value", "fileName", "href"):
            if value.get(key) not in (None, ""):
                return str(value.get(key)).strip()
        return ""
    if isinstance(value, list):
        parts = [_attr_value_as_text(v) for v in value]
        return ", ".join(p for p in parts if p)
    return str(value).strip()


def _attributes_by_alias(cp: dict[str, Any]) -> dict[str, str]:
    """Map MoySklad custom attributes onto CRM keys via Russian/English aliases."""
    out: dict[str, str] = {}
    raw = cp.get("attributes")
    if not isinstance(raw, list):
        return out
    for attr in raw:
        if not isinstance(attr, dict):
            continue
        name = str(attr.get("name") or "").strip().lower().replace("ё", "е")
        if not name:
            continue
        text = _attr_value_as_text(attr.get("value"))
        if not text:
            continue
        for key, aliases in _ATTR_ALIASES.items():
            if key in out:
                continue
            if name in aliases or any(a in name for a in aliases if len(a) >= 4):
                out[key] = text
                break
    return out


def _company_type_label(raw: Any) -> str:
    key = str(raw or "").strip().lower()
    if not key:
        return ""
    return _COMPANY_TYPE_LABELS.get(key, str(raw).strip())


def _sex_label(raw: Any) -> str:
    key = str(raw or "").strip().lower().replace("ё", "е")
    if not key:
        return ""
    return _SEX_LABELS.get(key, str(raw).strip())


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
    # Preserve order, unique
    seen_ch: set[str] = set()
    uniq_channels: list[str] = []
    for c in channels:
        key = str(c).strip().lower()
        if not key or key in seen_ch:
            continue
        seen_ch.add(key)
        uniq_channels.append(str(c).strip())
    channels = uniq_channels
    attrs = _attributes_by_alias(cp)
    sex = _sex_label(cp.get("sex")) or _sex_label(attrs.get("sex"))
    actual_address = str(cp.get("actualAddress") or cp.get("actual_address") or "").strip()
    # Remap balance is in kopecks; keep raw + rub for downstream risk facts.
    # Negative balance ⇒ counterparty owes the company (долг). Never invent.
    balance_raw = cp.get("balance")
    balance_rub = None
    if balance_raw is not None and balance_raw != "":
        try:
            balance_rub = round(float(balance_raw) / 100.0, 2)
        except (TypeError, ValueError):
            balance_rub = None
    tg_nick = attrs.get("tg_nick") or ""
    if not tg_nick:
        # Fallback: @nick in description / name
        blob = " ".join(
            str(x or "")
            for x in (cp.get("description"), cp.get("name"), cp.get("email"))
        )
        m = re.search(r"@([A-Za-z0-9_]{4,})", blob)
        if m:
            tg_nick = "@" + m.group(1)
    sales_type = sales_channel_type_from_channels(channels)
    return {
        "_moysklad_id": str(cp.get("id") or ""),
        "Наименование": str(cp.get("name") or "").strip(),
        "Телефон": str(cp.get("phone") or "").strip(),
        "E-mail": str(cp.get("email") or "").strip(),
        "email": str(cp.get("email") or "").strip(),
        "Группы": ", ".join(str(t) for t in tags if str(t).strip()),
        "_moysklad_tags": list(tags),
        "_moysklad_tags_display": ", ".join(str(t) for t in tags if str(t).strip()),
        "_moysklad_state": state_name,
        "Статус": state_name,
        "Статус контрагента": state_name,
        "_orders_context": [{"Канал продаж": c} for c in channels],
        "_order_channels_all": channels,
        "Канал продаж": ", ".join(channels),
        "Тип канала продаж": sales_type,
        "Тип продаж": sales_type,
        "Тип контрагента": _company_type_label(
            cp.get("companyType") or cp.get("company_type")
        ),
        "Пол": sex,
        "Фактический адрес": actual_address,
        "Фактический адрес (Комментарий)": attrs.get("actual_address_comment") or "",
        "Баллы начисленные": attrs.get("bonus_points") or "",
        "Заказчик или получатель": attrs.get("role") or "",
        "ТГ ник": tg_nick,
        "TG conversation": attrs.get("tg_conversation") or "",
        "balance": balance_rub,
        "balance_raw": balance_raw if balance_rub is not None else None,
    }
