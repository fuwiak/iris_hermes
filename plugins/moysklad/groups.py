"""Featured MoySklad group cloud (ported from Iris CRM export_format / fields).

Occasion / segment tags are shared across **Прямые** and **Маркетплейс** —
chip facets and filters must work for ``direct``, ``marketplace``, and ``all``.
Near-duplicate labels (``событие марта``/``март``, ``букет от 10000`` /
``букет от 10 000``) collapse to one canonical key for counts and matching.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from plugins.moysklad.sales_channels import (
    MARKETPLACE_AUDIENCE_GROUPS,
    _token_matches_any,
    moysklad_group_tokens,
)

# Shared occasion/segment chips — available on Прямые AND Маркетплейс.
SHARED_OCCASION_GROUPS = (
    "8 марта",
    "день мам",
    "букет от 10 000",
    "новый год",
    "цветы для интерьера",
    "флау вау",
    "скайлофт",
    "лофт гарден",
    "корпоративный клиент",
)

# Legacy direct-only extras kept for back-compat (subset of shared).
DIRECT_AUDIENCE_GROUPS = SHARED_OCCASION_GROUPS + ("день матери",)

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

# Canonical event labels use genitive only (avoid double-count марта/март).
EVENT_MONTH_GROUPS: tuple[str, ...] = tuple(
    f"событие {genitive}" for genitive, _nominative in _EVENT_MONTH_FORMS
)

_MONTH_ALIAS: dict[str, str] = {}
for genitive, nominative in _EVENT_MONTH_FORMS:
    canon = f"событие {genitive}"
    _MONTH_ALIAS[canon] = canon
    _MONTH_ALIAS[f"событие {nominative}"] = canon
    _MONTH_ALIAS[genitive] = canon
    _MONTH_ALIAS[nominative] = canon

_GROUP_ALIASES: dict[str, str] = {
    "день матери": "день мам",
    "день мамы": "день мам",
    "букет от 10000": "букет от 10 000",
    "букет от 10.000": "букет от 10 000",
    "букет от 10,000": "букет от 10 000",
    "букетот10000": "букет от 10 000",
    "флаувай": "флау вау",
    "флау вай": "флау вау",
    "flowwow": "флау вау",
    "flow wow": "флау вау",
    "флау вай скайлофт": "скайлофт",
    "флаувай скайлофт": "скайлофт",
    "skyloft": "скайлофт",
    "sky loft": "скайлофт",
}


def _compact(text: str) -> str:
    return re.sub(r"[\s.\-_,]", "", text.lower().replace("ё", "е"))


_GROUP_ALIASES_COMPACT: dict[str, str] = {
    _compact(k): v for k, v in _GROUP_ALIASES.items()
}


def normalize_group_key(name: str) -> str:
    """Canonical key for facet counts / filter equality (stage of tag dedupe)."""
    raw = str(name or "").strip().lower().replace("ё", "е")
    raw = re.sub(r"\s+", " ", raw)
    if not raw:
        return ""
    if raw in _GROUP_ALIASES:
        return _GROUP_ALIASES[raw]
    if raw in _MONTH_ALIAS:
        return _MONTH_ALIAS[raw]
    # «событие X» with either case form
    m = re.match(r"^событие\s+(.+)$", raw)
    if m:
        tail = m.group(1).strip()
        mapped = _MONTH_ALIAS.get(f"событие {tail}") or _MONTH_ALIAS.get(tail)
        if mapped:
            return mapped
    # Bouquet amount variants with arbitrary spacing/punctuation
    compact = _compact(raw)
    if "букет" in compact and "10000" in compact:
        return "букет от 10 000"
    if compact in _GROUP_ALIASES_COMPACT:
        return _GROUP_ALIASES_COMPACT[compact]
    return raw


def canonical_group_label(name: str) -> str:
    """Human display label for a (possibly aliased) group name."""
    key = normalize_group_key(name)
    if not key:
        return str(name or "").strip()
    # Prefer shared allowlist casing
    for label in SHARED_OCCASION_GROUPS + EVENT_MONTH_GROUPS:
        if normalize_group_key(label) == key:
            return label
    return key


def crm_featured_groups(sales_filter: str = "all") -> tuple[str, ...]:
    """Groups for the /clients chip cloud — shared occasions + event months.

    ``direct`` / ``marketplace`` / ``all`` all expose the same occasion facets
    so Прямые can filter by 8 марта, букет от 10 000, etc. Marketplace-only
    legacy aliases stay available under ``marketplace``/``all``.
    """
    key = (sales_filter or "all").strip().lower()
    source: list[str] = list(SHARED_OCCASION_GROUPS) + list(EVENT_MONTH_GROUPS)
    if key in ("marketplace", "маркетплейс", "маркетплейсы", "mp", "", "all"):
        source.extend(MARKETPLACE_AUDIENCE_GROUPS)
    # Always include day-mother alias target already covered; keep order stable.
    seen: set[str] = set()
    out: list[str] = []
    for name in source:
        canon = normalize_group_key(name)
        if not canon or canon in seen:
            continue
        seen.add(canon)
        out.append(canonical_group_label(name))
    return tuple(out)


def row_groups(row: dict[str, Any]) -> list[str]:
    """MoySklad tags only (not AI heuristics)."""
    return moysklad_group_tokens(row)


def row_has_group(row: dict[str, Any], group: str) -> bool:
    target = normalize_group_key(group)
    if not target:
        return True
    return any(normalize_group_key(n) == target for n in row_groups(row))


def group_chip_hue(name: str) -> int:
    return sum(ord(c) for c in name) % 360


def collect_featured_group_counts(
    rows: list[dict[str, Any]],
    *,
    sales_filter: str = "all",
    selected: str = "",
) -> list[dict[str, Any]]:
    """Chip cloud: shared occasions + event-<month>, counts after label normalize."""
    featured = crm_featured_groups(sales_filter)
    featured_keys = [normalize_group_key(label) for label in featured]
    counter: Counter[str] = Counter()
    display: dict[str, str] = {
        normalize_group_key(label): canonical_group_label(label) for label in featured
    }
    selected_name = str(selected or "").strip()
    selected_key = normalize_group_key(selected_name)
    if selected_key and selected_key not in display:
        display[selected_key] = canonical_group_label(selected_name) or selected_name

    event_re = re.compile(r"событи", re.IGNORECASE)
    for row in rows:
        groups = row_groups(row)
        if not groups:
            continue
        hit_keys: set[str] = set()
        for label in featured:
            label_key = normalize_group_key(label)
            if any(
                normalize_group_key(group) == label_key
                or _token_matches_any(group, (label, display.get(label_key, label)))
                for group in groups
            ):
                hit_keys.add(label_key)
        for group in groups:
            gkey = normalize_group_key(group)
            if event_re.search(group) or event_re.search(gkey):
                display.setdefault(gkey, canonical_group_label(group))
                hit_keys.add(gkey)
        if selected_key and any(normalize_group_key(g) == selected_key for g in groups):
            hit_keys.add(selected_key)
        for key in hit_keys:
            if key:
                counter[key] += 1

    items: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for label, key in zip(featured, featured_keys):
        count = int(counter.get(key, 0))
        if count <= 0 and key != selected_key:
            continue
        items.append({
            "name": display.get(key) or label,
            "count": count,
            "hue": group_chip_hue(display.get(key) or label),
            "source": "ms",
        })
        seen_keys.add(key)
    for key, count in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])):
        if key in seen_keys:
            continue
        label = display.get(key, key)
        if not event_re.search(label) and key != selected_key:
            continue
        items.append({
            "name": label,
            "count": int(count),
            "hue": group_chip_hue(label),
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
