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


def row_ai_groups(row: dict[str, Any]) -> list[str]:
    """Groups filled by AI overlay for this counterparty (may be empty)."""
    cid = str(row.get("_moysklad_id") or row.get("id") or "").strip()
    if not cid:
        return []
    try:
        from plugins.moysklad.ai_fill import ai_group_labels_for_client

        return ai_group_labels_for_client(cid)
    except Exception:
        return []


def row_all_groups(row: dict[str, Any]) -> list[str]:
    """MoySklad tags ∪ AI overlay groups (deduped by canonical key)."""
    seen: set[str] = set()
    out: list[str] = []
    for name in list(row_groups(row)) + list(row_ai_groups(row)):
        key = normalize_group_key(name)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(canonical_group_label(name))
    return out


def row_has_group(
    row: dict[str, Any],
    group: str,
    *,
    source: str = "any",
) -> bool:
    """Match group by canonical key, optionally scoped to МС / AI tokens."""
    target = normalize_group_key(group)
    if not target:
        return True
    src = (source or "any").strip().lower().replace("ё", "е")
    if src in ("ms", "moysklad", "мойсклад", "мск", "mcs"):
        tokens = row_groups(row)
    elif src in ("ai", "ии", "llm", "heuristic"):
        tokens = row_ai_groups(row)
    else:
        tokens = row_all_groups(row)
    return any(normalize_group_key(n) == target for n in tokens)


def split_group_options_by_source(
    items: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Split chip cloud into МС / AI lists for separate UI filter sections.

    ``both`` chips appear in both lists with the corresponding count.
    """
    ms_items: list[dict[str, Any]] = []
    ai_items: list[dict[str, Any]] = []
    for item in items or []:
        source = str(item.get("source") or "ms")
        ms_n = int(item.get("ms_count") or 0)
        ai_n = int(item.get("ai_count") or 0)
        if source in ("ms", "both") or ms_n > 0:
            ms_items.append({
                **item,
                "count": ms_n or int(item.get("count") or 0),
                "filter_source": "ms",
            })
        if source in ("ai", "both") or ai_n > 0:
            ai_items.append({
                **item,
                "count": ai_n or int(item.get("count") or 0),
                "filter_source": "ai",
            })
    return {"ms": ms_items, "ai": ai_items}


def group_chip_hue(name: str) -> int:
    return sum(ord(c) for c in name) % 360


def _count_group_hits(
    rows: list[dict[str, Any]],
    *,
    featured: tuple[str, ...],
    selected_key: str,
) -> tuple[Counter[str], Counter[str], dict[str, str]]:
    """Return (ms_counter, ai_counter, display_label_by_key)."""
    featured_keys = {normalize_group_key(label): label for label in featured}
    display: dict[str, str] = {
        normalize_group_key(label): canonical_group_label(label) for label in featured
    }
    ms_counter: Counter[str] = Counter()
    ai_counter: Counter[str] = Counter()
    event_re = re.compile(r"событи", re.IGNORECASE)

    def _hits_for(tokens: list[str], *, include_all: bool) -> set[str]:
        hit_keys: set[str] = set()
        for label in featured:
            label_key = normalize_group_key(label)
            if any(
                normalize_group_key(group) == label_key
                or _token_matches_any(group, (label, display.get(label_key, label)))
                for group in tokens
            ):
                hit_keys.add(label_key)
        for group in tokens:
            gkey = normalize_group_key(group)
            if not gkey:
                continue
            if include_all or event_re.search(group) or event_re.search(gkey):
                display.setdefault(gkey, canonical_group_label(group))
                hit_keys.add(gkey)
            elif gkey in featured_keys:
                hit_keys.add(gkey)
                display.setdefault(gkey, canonical_group_label(featured_keys[gkey]))
            elif gkey == selected_key:
                display.setdefault(gkey, canonical_group_label(group))
                hit_keys.add(gkey)
        return hit_keys

    for row in rows:
        ms_tokens = row_groups(row)
        ai_tokens = row_ai_groups(row)
        for key in _hits_for(ms_tokens, include_all=False):
            if key:
                ms_counter[key] += 1
        for key in _hits_for(ai_tokens, include_all=True):
            if key:
                ai_counter[key] += 1
        if selected_key and selected_key not in display:
            display[selected_key] = canonical_group_label(selected_key)

    return ms_counter, ai_counter, display


def collect_featured_group_counts(
    rows: list[dict[str, Any]],
    *,
    sales_filter: str = "all",
    selected: str = "",
) -> list[dict[str, Any]]:
    """Chip cloud: MoySklad (МС) + AI overlay groups, one chip per canonical key.

    Each item has ``source``: ``ms`` | ``ai`` | ``both``.
    """
    featured = crm_featured_groups(sales_filter)
    featured_keys = [normalize_group_key(label) for label in featured]
    selected_name = str(selected or "").strip()
    selected_key = normalize_group_key(selected_name)
    ms_counter, ai_counter, display = _count_group_hits(
        rows, featured=featured, selected_key=selected_key
    )
    if selected_key and selected_key not in display:
        display[selected_key] = canonical_group_label(selected_name) or selected_name

    all_keys: list[str] = []
    seen: set[str] = set()
    for key in featured_keys:
        if key and key not in seen:
            seen.add(key)
            all_keys.append(key)
    for key in sorted(
        set(ms_counter) | set(ai_counter) | ({selected_key} if selected_key else set()),
        key=lambda k: (-(ms_counter.get(k, 0) + ai_counter.get(k, 0)), k),
    ):
        if not key or key in seen:
            continue
        label = display.get(key, key)
        # MS: featured/events only (already in featured_keys loop).
        # AI: also surface AI-only tags like «премиум» / «новый».
        if (
            key == selected_key
            or re.search(r"событи", label, re.I)
            or ai_counter.get(key, 0) > 0
        ):
            seen.add(key)
            all_keys.append(key)

    items: list[dict[str, Any]] = []
    for key in all_keys:
        ms_n = int(ms_counter.get(key, 0))
        ai_n = int(ai_counter.get(key, 0))
        if ms_n <= 0 and ai_n <= 0 and key != selected_key:
            continue
        if ms_n > 0 and ai_n > 0:
            source = "both"
            count = max(ms_n, ai_n)
        elif ai_n > 0:
            source = "ai"
            count = ai_n
        else:
            source = "ms"
            count = ms_n
        label = display.get(key) or key
        items.append({
            "name": label,
            "count": int(count),
            "ms_count": ms_n,
            "ai_count": ai_n,
            "hue": group_chip_hue(label),
            "source": source,
        })
    return items
