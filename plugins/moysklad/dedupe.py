"""Multi-stage deduplication for MoySklad clients / catalog rows.

Stages (applied in order; later stages only see survivors of earlier ones):

1. **Canonical id** — MoySklad counterparty ``id`` / href tail. Same id →
   merge into the richer row (update-in-place; never append).
2. **Contact keys** — normalized phone, email, telegram handle. Distinct ids
   that share a contact key collapse to one row.
3. **Fuzzy name+phone** — same normalized name + phone stem within the batch
   (catches near-duplicates that skipped stage 2).
4. **Cache / page merge** — ``merge_rows`` / ``merge_catalogs`` update by
   canonical id; never append a row whose id (or contact key) already exists.

Counts and ``matched_total`` must be computed **after** this pipeline.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

_PHONE_DIGITS = re.compile(r"\D+")
_WS = re.compile(r"\s+")


def canonical_id(row: dict[str, Any]) -> str:
    """Stage 1 key: MoySklad counterparty id (or href tail)."""
    for key in ("_moysklad_id", "id"):
        raw = row.get(key)
        if raw not in (None, ""):
            return str(raw).strip()
    meta = row.get("meta")
    if isinstance(meta, dict):
        href = str(meta.get("href") or "").strip()
        if href:
            return href.rstrip("/").rsplit("/", 1)[-1]
    return ""


def normalize_phone(value: Any) -> str:
    """Normalize phone to a stable digit key (RU 11-digit → last 10)."""
    digits = _PHONE_DIGITS.sub("", str(value or ""))
    if not digits:
        return ""
    if len(digits) >= 11 and digits[0] in ("7", "8"):
        digits = digits[-10:]
    elif len(digits) > 10:
        digits = digits[-10:]
    return digits if len(digits) >= 7 else ""


def normalize_email(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text or "@" not in text:
        return ""
    return text


def normalize_telegram(value: Any) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
    if not text:
        return ""
    text = text.replace("https://t.me/", "").replace("http://t.me/", "")
    text = text.replace("t.me/", "").lstrip("@")
    text = text.split("?")[0].split("/")[0].strip()
    if not text or text in {"c", "joinchat"}:
        return ""
    # Digits-only chat ids are weak merge keys — skip.
    if text.isdigit():
        return ""
    return text


def normalize_name(value: Any) -> str:
    text = _WS.sub(" ", str(value or "").strip().lower().replace("ё", "е"))
    return text


def contact_keys(row: dict[str, Any]) -> list[str]:
    """Stage 2 keys: phone:… / email:… / tg:… (non-empty only)."""
    keys: list[str] = []
    phone = normalize_phone(row.get("Телефон") or row.get("phone"))
    if phone:
        keys.append(f"phone:{phone}")
    email = normalize_email(
        row.get("email") or row.get("E-mail") or row.get("Email")
    )
    if email:
        keys.append(f"email:{email}")
    tg = normalize_telegram(
        row.get("ТГ ник")
        or row.get("tg_nick")
        or row.get("TG conversation")
        or row.get("tg_conversation")
    )
    if tg:
        keys.append(f"tg:{tg}")
    return keys


def fuzzy_name_phone_key(row: dict[str, Any]) -> str:
    """Stage 3 key: name + phone stem (empty if either side missing)."""
    name = normalize_name(row.get("Наименование") or row.get("name"))
    phone = normalize_phone(row.get("Телефон") or row.get("phone"))
    if not name or not phone:
        return ""
    # Stem: first 7 digits — tolerates trailing extension noise.
    stem = phone[:7]
    return f"fuzzy:{name}|{stem}"


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    return [value]


def _merge_unique_str(a: Any, b: Any) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in _as_list(a) + _as_list(b):
        text = str(item or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _richness(row: dict[str, Any]) -> tuple[int, int, int]:
    """Prefer rows with more orders, then more non-empty public fields."""
    orders = _as_list(row.get("_orders_context"))
    order_count = int(row.get("order_count") or row.get("Всего заказов") or len(orders) or 0)
    filled = sum(
        1
        for key in (
            "Телефон",
            "phone",
            "email",
            "E-mail",
            "ТГ ник",
            "tg_nick",
            "Фактический адрес",
            "actual_address",
            "Наименование",
            "name",
        )
        if str(row.get(key) or "").strip()
    )
    tags = len(_as_list(row.get("_moysklad_tags") or row.get("tags")))
    return (order_count, filled, tags)


def merge_client_rows(keep: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Update-in-place merge: richer row wins base; fill blanks from loser."""
    if _richness(incoming) > _richness(keep):
        base, other = dict(incoming), keep
    else:
        base, other = dict(keep), incoming

    # Preserve canonical id of the keep/base winner when both present.
    kid = canonical_id(keep) or canonical_id(incoming)
    if kid:
        base["_moysklad_id"] = kid
        if "id" in base or "id" in other:
            base["id"] = kid

    for key, value in other.items():
        if key.startswith("_") and key not in (
            "_orders_context",
            "_moysklad_tags",
            "_moysklad_tags_display",
            "_moysklad_state",
            "_moysklad_id",
            "_order_channels_all",
            "_audience",
            "_comment_blob",
        ):
            continue
        cur = base.get(key)
        if key in ("_orders_context",):
            # Dedupe orders by id/name+moment inside context list.
            merged_orders: list[dict[str, Any]] = []
            seen_oids: set[str] = set()
            for order in _as_list(cur) + _as_list(value):
                if not isinstance(order, dict):
                    continue
                oid = str(order.get("id") or "").strip()
                fallback = "|".join(
                    str(order.get(k) or "")
                    for k in ("moment", "Дата", "name", "Сумма", "sum", "channel")
                )
                token = oid or fallback
                if token in seen_oids:
                    continue
                seen_oids.add(token)
                merged_orders.append(order)
            base[key] = merged_orders
            continue
        if key in ("_moysklad_tags", "tags", "channels", "_order_channels_all"):
            base[key] = _merge_unique_str(cur, value)
            continue
        if key in ("Группы", "_moysklad_tags_display"):
            parts = _merge_unique_str(
                str(cur or "").split(","),
                str(value or "").split(","),
            )
            base[key] = ", ".join(parts)
            continue
        if cur in (None, "", [], {}) and value not in (None, "", [], {}):
            base[key] = value

    # Recompute order aggregates when context present.
    ctx = _as_list(base.get("_orders_context"))
    if ctx:
        amounts: list[float] = []
        last = ""
        for item in ctx:
            if not isinstance(item, dict):
                continue
            try:
                amount = float(item.get("sum") or item.get("Сумма") or 0)
            except (TypeError, ValueError):
                amount = 0.0
            if amount > 0:
                amounts.append(amount)
            moment = str(item.get("moment") or item.get("Дата") or "").strip()
            if moment and moment > last:
                last = moment
        base["order_count"] = len(ctx)
        base["Всего заказов"] = len(ctx)
        if amounts:
            avg = round(sum(amounts) / len(amounts), 2)
            base["avg_check"] = avg
            base["Средний чек"] = avg
        if last:
            base["last_order_at"] = last
            base["Дата последнего заказа"] = last

    return base


def dedupe_by_canonical_id(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stage 1: collapse identical MoySklad ids (update-in-place)."""
    by_id: dict[str, dict[str, Any]] = {}
    no_id: list[dict[str, Any]] = []
    order: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        cid = canonical_id(row)
        if not cid:
            no_id.append(dict(row))
            continue
        if cid in by_id:
            by_id[cid] = merge_client_rows(by_id[cid], row)
        else:
            by_id[cid] = dict(row)
            order.append(cid)
    return [by_id[i] for i in order] + no_id


def _collapse_by_keys(
    rows: list[dict[str, Any]],
    key_fn,
) -> list[dict[str, Any]]:
    """Generic union-find-ish collapse: first row wins slot; merge on key hit."""
    result: list[dict[str, Any]] = []
    index_by_key: dict[str, int] = {}
    id_to_index: dict[str, int] = {}

    for row in rows:
        row = dict(row)
        cid = canonical_id(row)
        keys = [k for k in key_fn(row) if k]
        target: int | None = None
        if cid and cid in id_to_index:
            target = id_to_index[cid]
        if target is None:
            for key in keys:
                if key in index_by_key:
                    target = index_by_key[key]
                    break
        if target is None:
            idx = len(result)
            result.append(row)
            if cid:
                id_to_index[cid] = idx
            for key in keys:
                index_by_key[key] = idx
            continue
        merged = merge_client_rows(result[target], row)
        result[target] = merged
        new_cid = canonical_id(merged)
        if new_cid:
            id_to_index[new_cid] = target
        for key in contact_keys(merged) + (
            [fuzzy_name_phone_key(merged)] if fuzzy_name_phone_key(merged) else []
        ):
            if key:
                index_by_key[key] = target
    return result


def dedupe_by_contact_keys(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stage 2: merge rows that share phone / email / telegram."""
    return _collapse_by_keys(list(rows), contact_keys)


def dedupe_by_fuzzy_name_phone(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stage 3: merge name+phone collisions within the batch."""

    def keys(row: dict[str, Any]) -> list[str]:
        key = fuzzy_name_phone_key(row)
        return [key] if key else []

    return _collapse_by_keys(list(rows), keys)


def recompute_audience_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Rebuild tab counts from deduped rows (import matcher lazily)."""
    from plugins.moysklad.sales_channels import row_matches_sales_filter

    counts = {"direct": 0, "marketplace": 0, "other": 0, "total": 0}
    for row in rows:
        is_direct = row_matches_sales_filter(row, "direct")
        is_mp = row_matches_sales_filter(row, "marketplace")
        row["_audience"] = {"direct": is_direct, "marketplace": is_mp}
        counts["total"] += 1
        if is_direct:
            counts["direct"] += 1
        if is_mp:
            counts["marketplace"] += 1
        if not is_direct and not is_mp:
            counts["other"] += 1
    return counts


def dedupe_catalog_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run stages 1→3 on CRM rows."""
    stage1 = dedupe_by_canonical_id(rows)
    stage2 = dedupe_by_contact_keys(stage1)
    stage3 = dedupe_by_fuzzy_name_phone(stage2)
    return stage3


def merge_rows(
    existing: Iterable[dict[str, Any]],
    incoming: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Stage 4: merge two row lists without appending duplicates."""
    return dedupe_catalog_rows(list(existing) + list(incoming))


def merge_catalogs(
    existing: dict[str, Any] | None,
    incoming: dict[str, Any],
) -> dict[str, Any]:
    """Stage 4 for full catalog envelopes: update-in-place by id/contact."""
    base_rows = list((existing or {}).get("rows") or [])
    new_rows = list(incoming.get("rows") or [])
    merged_rows = merge_rows(base_rows, new_rows)
    counts = recompute_audience_counts(merged_rows)
    out = dict(incoming)
    out["rows"] = merged_rows
    out["counts"] = counts
    # Prefer max scanned counters when merging incremental syncs.
    for key in ("orders_scanned", "counterparties_scanned"):
        out[key] = max(
            int((existing or {}).get(key) or 0),
            int(incoming.get(key) or 0),
            int(out.get(key) or 0),
        )
    return out


def dedupe_entity_pages(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dedupe raw MoySklad API entities by ``id`` (fetch_all safety)."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        rid = str(row.get("id") or "").strip()
        if rid:
            if rid in seen:
                continue
            seen.add(rid)
        out.append(row)
    return out
