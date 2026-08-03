"""Featured MoySklad group cloud (ported from Iris CRM export_format / fields)."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from plugins.moysklad.sales_channels import (
    MARKETPLACE_AUDIENCE_GROUPS,
    _token_matches_any,
    moysklad_group_tokens,
)

DIRECT_AUDIENCE_GROUPS = (
    "8 марта",
    "день мам",
    "день матери",
    "лофт гарден",
    "новый год",
    "цветы для интерьера",
    "корпоративный клиент",
)

_EVENT_MONTH_FORMS = (
    ("января", "январь"),
    ("февраля", "февраль"),
    ("марта", "март"),
    ("апреля", "апрель"),
    ("мая", "май"),
    ("июня", "июнь"),
    ("июля", "июль"),
    ("августа", "август"),
    ("сентября", "сентябрь"),
    ("октября", "октябрь"),
    ("ноября", "ноябрь"),
    ("декабря", "декабрь"),
)

EVENT_MONTH_GROUPS: tuple[str, ...] = tuple(
    f"событие {form}"
    for genitive, nominative in _EVENT_MONTH_FORMS
    for form in (genitive, nominative)
)


def crm_featured_groups(sales_filter: str = "all") -> tuple[str, ...]:
    """Groups for the /clients chip cloud — TZ allowlist + event months."""
    key = (sales_filter or "all").strip().lower()
    if key in ("direct", "прямые", "прямые продажи"):
        source = DIRECT_AUDIENCE_GROUPS + EVENT_MONTH_GROUPS
    elif key in ("marketplace", "маркетплейс", "маркетплейсы", "mp"):
        source = MARKETPLACE_AUDIENCE_GROUPS + EVENT_MONTH_GROUPS
    else:
        source = DIRECT_AUDIENCE_GROUPS + MARKETPLACE_AUDIENCE_GROUPS + EVENT_MONTH_GROUPS
    seen: set[str] = set()
    out: list[str] = []
    for name in source:
        token = name.strip().lower().replace("ё", "е")
        if token and token not in seen:
            seen.add(token)
            out.append(name)
    return tuple(out)


def row_groups(row: dict[str, Any]) -> list[str]:
    """MoySklad tags only (not AI heuristics)."""
    return moysklad_group_tokens(row)


def row_has_group(row: dict[str, Any], group: str) -> bool:
    target = group.strip().lower()
    if not target:
        return True
    return any(n.lower() == target for n in row_groups(row))


def group_chip_hue(name: str) -> int:
    return sum(ord(c) for c in name) % 360


def collect_featured_group_counts(
    rows: list[dict[str, Any]],
    *,
    sales_filter: str = "all",
    selected: str = "",
) -> list[dict[str, Any]]:
    """Chip cloud: TZ segments + event-<month> groups present on rows."""
    featured = crm_featured_groups(sales_filter)
    counter: Counter[str] = Counter()
    display: dict[str, str] = {label.lower(): label for label in featured}
    selected_name = str(selected or "").strip()
    selected_key = selected_name.lower()
    if selected_key and selected_key not in display:
        display[selected_key] = selected_name

    event_re = re.compile(r"событи", re.IGNORECASE)
    for row in rows:
        groups = row_groups(row)
        if not groups:
            continue
        hit_keys: set[str] = set()
        for label in featured:
            if any(_token_matches_any(group, (label,)) for group in groups):
                hit_keys.add(label.lower())
        for group in groups:
            if event_re.search(group):
                key = group.lower()
                display.setdefault(key, group)
                hit_keys.add(key)
        if selected_key and any(g.lower() == selected_key for g in groups):
            hit_keys.add(selected_key)
        for key in hit_keys:
            counter[key] += 1

    items: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for label in featured:
        key = label.lower()
        count = int(counter.get(key, 0))
        if count <= 0 and key != selected_key:
            continue
        items.append({
            "name": display[key],
            "count": count,
            "hue": group_chip_hue(display[key]),
            "source": "ms",
        })
        seen_keys.add(key)
    for key, count in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])):
        if key in seen_keys:
            continue
        if not event_re.search(display.get(key, key)) and key != selected_key:
            continue
        items.append({
            "name": display[key],
            "count": int(count),
            "hue": group_chip_hue(display[key]),
            "source": "ms",
        })
        seen_keys.add(key)
    if selected_key and selected_key not in seen_keys:
        items.append({
            "name": display[selected_key],
            "count": int(counter.get(selected_key, 0)),
            "hue": group_chip_hue(display[selected_key]),
            "source": "ms",
        })
    return items
