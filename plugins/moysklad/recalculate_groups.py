"""LLM-assisted group taxonomy propose + reassign (MVP).

Flow:
1. ``propose_taxonomy`` — LLM (or heuristic fallback) suggests group names
   from existing tags / order facts in the current audience.
2. User edits the list in UI.
3. ``assign_to_taxonomy`` — map each client to best-fitting labels using
   facts/orders + keyword overlap (no invented contacts); optional push
   via ``assign_groups.push_merged_tags``.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home
from plugins.moysklad.assign_groups import heuristic_groups_for_row, merge_tags
from plugins.moysklad.groups import (
    SHARED_OCCASION_GROUPS,
    canonical_group_label,
    normalize_group_key,
)
from plugins.moysklad.sales_channels import moysklad_group_tokens

log = logging.getLogger(__name__)

_TAXONOMY_PATH_REL = Path("moysklad") / "group_taxonomy.json"

_PROPOSE_SYSTEM = """Ты помощник CRM цветочного магазина.
По списку существующих тегов МойСклад и кратким фактам предложи
короткий список имён групп (taxonomy) на русском.

Правила:
1. Только группы, которые можно проверить по тегам/заказам/поводам —
   не выдумывай каналы, телефоны, VIP.
2. Объединяй дубликаты (событие марта/март, букет от 10000 / 10 000).
3. 8–18 коротких названий, без маркетингового шума.
4. Ответ — строго JSON: {"groups": ["имя1", "имя2", ...]} без markdown.
"""


def _taxonomy_path() -> Path:
    root = get_hermes_home() / "moysklad"
    root.mkdir(parents=True, exist_ok=True)
    return root / "group_taxonomy.json"


def load_taxonomy() -> list[str]:
    path = _taxonomy_path()
    if not path.is_file():
        return list(SHARED_OCCASION_GROUPS)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return list(SHARED_OCCASION_GROUPS)
    groups = raw.get("groups") if isinstance(raw, dict) else raw
    if not isinstance(groups, list):
        return list(SHARED_OCCASION_GROUPS)
    out: list[str] = []
    seen: set[str] = set()
    for item in groups:
        label = canonical_group_label(str(item or "").strip())
        key = normalize_group_key(label)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(label)
    return out or list(SHARED_OCCASION_GROUPS)


def save_taxonomy(groups: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in groups:
        label = canonical_group_label(str(item or "").strip())
        key = normalize_group_key(label)
        if not key or key in seen:
            continue
        seen.add(key)
        cleaned.append(label)
    path = _taxonomy_path()
    path.write_text(
        json.dumps({"groups": cleaned}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return cleaned


def _tag_frequencies(rows: list[dict[str, Any]], *, limit: int = 80) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    for row in rows[:5000]:
        for tag in moysklad_group_tokens(row):
            key = normalize_group_key(tag)
            if key:
                counter[key] += 1
    items = []
    for key, count in counter.most_common(limit):
        items.append({"name": canonical_group_label(key), "count": count})
    return items


def _heuristic_propose(rows: list[dict[str, Any]]) -> list[str]:
    freqs = _tag_frequencies(rows)
    names = [item["name"] for item in freqs[:16]]
    for seed in SHARED_OCCASION_GROUPS:
        if normalize_group_key(seed) not in {normalize_group_key(n) for n in names}:
            names.append(seed)
        if len(names) >= 18:
            break
    return names[:18]


def propose_taxonomy(
    rows: list[dict[str, Any]],
    *,
    sales_filter: str = "all",
) -> dict[str, Any]:
    """Return proposed group names + tag frequencies (LLM with heuristic fallback)."""
    freqs = _tag_frequencies(rows)
    fallback = _heuristic_propose(rows)
    sample_facts = []
    for row in rows[:40]:
        sample_facts.append({
            "name": row.get("Наименование") or row.get("name") or "",
            "tags": moysklad_group_tokens(row)[:8],
            "order_count": row.get("order_count") or row.get("Всего заказов") or 0,
            "avg_check": row.get("avg_check") or row.get("Средний чек") or 0,
            "channels": [
                str((o or {}).get("Канал продаж") or "")
                for o in (row.get("_orders_context") or [])[:3]
                if isinstance(o, dict)
            ],
        })
    user = json.dumps(
        {
            "sales_filter": sales_filter,
            "tag_frequencies": freqs[:40],
            "sample_clients": sample_facts,
            "seed_groups": list(SHARED_OCCASION_GROUPS),
        },
        ensure_ascii=False,
    )
    groups = fallback
    source = "heuristic"
    try:
        from agent.auxiliary_client import call_llm, extract_content_or_reasoning

        response = call_llm(
            task="compression",
            messages=[
                {"role": "system", "content": _PROPOSE_SYSTEM},
                {"role": "user", "content": user},
            ],
            max_tokens=700,
            temperature=0.2,
            timeout=45.0,
        )
        text = (extract_content_or_reasoning(response) or "").strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                text = "\n".join(
                    lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
                ).strip()
        data = json.loads(text) if text.startswith("{") else None
        if data is None:
            m = re.search(r"\{[\s\S]*\}", text or "")
            data = json.loads(m.group(0)) if m else None
        raw_groups = (data or {}).get("groups") if isinstance(data, dict) else None
        if isinstance(raw_groups, list) and raw_groups:
            cleaned: list[str] = []
            seen: set[str] = set()
            for item in raw_groups:
                label = canonical_group_label(str(item or "").strip())
                key = normalize_group_key(label)
                if not key or key in seen:
                    continue
                seen.add(key)
                cleaned.append(label)
            if cleaned:
                groups = cleaned
                source = "llm"
    except Exception as exc:
        log.warning("moysklad recalculate propose LLM unavailable: %s", exc)

    return {
        "ok": True,
        "source": source,
        "groups": groups,
        "tag_frequencies": freqs,
        "audience_rows": len(rows),
        "sales_filter": sales_filter,
    }


def _score_group(row: dict[str, Any], group: str) -> int:
    """How well a taxonomy label fits this client (facts only)."""
    key = normalize_group_key(group)
    if not key:
        return 0
    score = 0
    tags = {normalize_group_key(t) for t in moysklad_group_tokens(row)}
    if key in tags:
        score += 5
    proposed = {normalize_group_key(t) for t in heuristic_groups_for_row(row)}
    if key in proposed:
        score += 3
    blob = " ".join(
        [
            str(row.get("Наименование") or ""),
            str(row.get("description") or row.get("_comment_blob") or ""),
            " ".join(moysklad_group_tokens(row)),
        ]
    ).lower().replace("ё", "е")
    needle = key.replace("событие ", "")
    if needle and needle in blob:
        score += 1
    return score


def assign_to_taxonomy(
    rows: list[dict[str, Any]],
    groups: list[str],
    *,
    max_labels_per_client: int = 4,
) -> list[dict[str, Any]]:
    """Assign taxonomy labels to rows; merge with existing MoySklad tags."""
    taxonomy = []
    seen: set[str] = set()
    for g in groups:
        label = canonical_group_label(str(g or "").strip())
        key = normalize_group_key(label)
        if not key or key in seen:
            continue
        seen.add(key)
        taxonomy.append(label)

    results: list[dict[str, Any]] = []
    for row in rows:
        cp_id = str(row.get("_moysklad_id") or row.get("id") or "").strip()
        if not cp_id:
            continue
        existing = list(row.get("_moysklad_tags") or moysklad_group_tokens(row))
        scored = sorted(
            ((g, _score_group(row, g)) for g in taxonomy),
            key=lambda pair: (-pair[1], pair[0]),
        )
        picked = [g for g, sc in scored if sc > 0][:max_labels_per_client]
        # Always keep heuristic signals that intersect taxonomy.
        for h in heuristic_groups_for_row(row):
            hk = normalize_group_key(h)
            for g in taxonomy:
                if normalize_group_key(g) == hk and g not in picked:
                    picked.append(g)
        merged = merge_tags(existing, picked)
        # Prefer canonical labels in merged list
        merged_canon = []
        mseen: set[str] = set()
        for tag in merged:
            label = canonical_group_label(tag)
            key = normalize_group_key(label)
            if not key or key in mseen:
                continue
            mseen.add(key)
            merged_canon.append(label)
        added = [
            t
            for t in merged_canon
            if normalize_group_key(t)
            not in {normalize_group_key(e) for e in existing}
        ]
        results.append({
            "id": cp_id,
            "name": row.get("Наименование") or row.get("name") or "",
            "existing": existing,
            "proposed": picked,
            "added": added,
            "merged": merged_canon,
            "changed": bool(added),
        })
    return results
