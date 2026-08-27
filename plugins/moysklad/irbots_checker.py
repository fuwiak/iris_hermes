"""IRbots Telegram Number Checker — phone status with durable cache.

API: ``http://api.irbots.com`` (``target=checker``). Docs:
https://irbots.com/TG-Number-Analyzer/#api-section
https://telegra.ph/Checker-API-03-26

Statuses (observed + FAQ):

* ``session`` / ``used`` — has Telegram session → active
* ``true`` — unregistered (True) → inactive
* ``banned`` — blocked → inactive
* ``invalid`` / ``error`` — bad number → inactive

Cache is **by phone** (not client id) so duplicate numbers across CRM rows
never burn a second credit. Persistence:
memory → Redis → ``$HERMES_HOME/moysklad/irbots_phone_cache.json``.

Env: ``IRBOTS_API_KEY`` (required). Optional ``IRBOTS_API_URL``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from hermes_constants import get_hermes_home
from plugins.moysklad.dedupe import all_normalized_phones, normalize_phone
from plugins.moysklad.conversations import normalize_tg_nick

log = logging.getLogger(__name__)

_CACHE_NAME = "irbots_phone_cache.json"
_REDIS_KEY = "moysklad:irbots:phone_cache:v1"
_DEFAULT_URL = "http://api.irbots.com"
# api.irbots.com is GET-only; URI blows past ~450 numbers (HTTP 414).
# Stay well under that. Telegraph POST caps at 120.
_MAX_PER_REQUEST = 200
_CACHE_LOCK = threading.RLock()
_MEMORY: dict[str, Any] | None = None
_MEMORY_FP: str | None = None

# String "true" / boolean True = unregistered (IRbots FAQ «True»).
# «session» / «used» = has Telegram session → active.
_ACTIVE_STRINGS = frozenset({"session", "used", "active", "yes", "1", "ok"})
_INACTIVE_STRINGS = frozenset(
    {
        "true",  # unregistered (also JSON boolean True)
        "true_bool",
        "banned",
        "ban",
        "invalid",
        "error",
        "spam",
        "false",
        "false_bool",
        "no",
        "0",
        "unregistered",
        "empty",
    }
)


def api_key() -> str:
    return (os.environ.get("IRBOTS_API_KEY") or "").strip()


def api_key_configured() -> bool:
    return bool(api_key())


def api_url() -> str:
    return (os.environ.get("IRBOTS_API_URL") or _DEFAULT_URL).strip() or _DEFAULT_URL


def _account_fingerprint() -> str:
    token = (os.environ.get("MOYSKLAD_API_TOKEN") or "").strip()
    if not token:
        return "no-token"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _redis_url() -> str:
    return (os.environ.get("REDIS_URL") or os.environ.get("MOYSKLAD_REDIS_URL") or "").strip()


def _redis_client():
    url = _redis_url()
    if not url:
        return None
    try:
        import redis  # type: ignore[import-not-found]
    except Exception:
        return None
    try:
        client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2.0)
        client.ping()
        return client
    except Exception as exc:
        log.warning("IRbots phone-cache Redis unavailable (%s); file cache", exc)
        return None


def _cache_path() -> Path:
    root = get_hermes_home() / "moysklad"
    root.mkdir(parents=True, exist_ok=True)
    return root / _CACHE_NAME


def _redis_key() -> str:
    return f"{_REDIS_KEY}:{_account_fingerprint()}"


def _empty_cache() -> dict[str, Any]:
    return {"by_phone": {}, "stats": {}, "saved_at": 0.0}


def _normalize_cache(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return _empty_cache()
    by_phone = raw.get("by_phone") if isinstance(raw.get("by_phone"), dict) else {}
    return {
        "by_phone": by_phone,
        "stats": raw.get("stats") if isinstance(raw.get("stats"), dict) else {},
        "saved_at": float(raw.get("saved_at") or 0),
    }


def _memory_fingerprint() -> str:
    return f"{_account_fingerprint()}:{_cache_path()}"


def load_phone_cache() -> dict[str, Any]:
    global _MEMORY, _MEMORY_FP
    fp = _memory_fingerprint()
    with _CACHE_LOCK:
        if (
            _MEMORY_FP == fp
            and isinstance(_MEMORY, dict)
            and _MEMORY.get("by_phone") is not None
        ):
            return {
                "by_phone": dict(_MEMORY.get("by_phone") or {}),
                "stats": dict(_MEMORY.get("stats") or {}),
                "saved_at": float(_MEMORY.get("saved_at") or 0),
            }

    client = _redis_client()
    if client is not None:
        try:
            raw = client.get(_redis_key())
            if raw:
                cache = _normalize_cache(json.loads(raw))
                with _CACHE_LOCK:
                    _MEMORY = cache
                    _MEMORY_FP = fp
                return {
                    "by_phone": dict(cache.get("by_phone") or {}),
                    "stats": dict(cache.get("stats") or {}),
                    "saved_at": float(cache.get("saved_at") or 0),
                }
        except Exception as exc:
            log.warning("IRbots Redis load failed: %s", exc)

    path = _cache_path()
    if path.is_file():
        try:
            cache = _normalize_cache(json.loads(path.read_text(encoding="utf-8")))
            with _CACHE_LOCK:
                _MEMORY = cache
                _MEMORY_FP = fp
            return {
                "by_phone": dict(cache.get("by_phone") or {}),
                "stats": dict(cache.get("stats") or {}),
                "saved_at": float(cache.get("saved_at") or 0),
            }
        except Exception as exc:
            log.warning("IRbots file cache load failed: %s", exc)

    empty = _empty_cache()
    with _CACHE_LOCK:
        _MEMORY = empty
        _MEMORY_FP = fp
    return {"by_phone": {}, "stats": {}, "saved_at": 0.0}


def save_phone_cache(cache: dict[str, Any]) -> None:
    global _MEMORY, _MEMORY_FP
    normalized = _normalize_cache(cache)
    normalized["saved_at"] = time.time()
    payload = json.dumps(normalized, ensure_ascii=False, default=str)

    with _CACHE_LOCK:
        _MEMORY = normalized
        _MEMORY_FP = _memory_fingerprint()

    client = _redis_client()
    if client is not None:
        try:
            client.set(_redis_key(), payload)
        except Exception as exc:
            log.warning("IRbots Redis save failed: %s", exc)

    try:
        _cache_path().write_text(payload, encoding="utf-8")
    except Exception as exc:
        log.warning("IRbots file cache save failed: %s", exc)


def phone_to_e164(raw: Any) -> str:
    """Digits → ``+7…`` E.164 for the checker (RU last-10 → +7)."""
    digits = re.sub(r"\D", "", str(raw or ""))
    if not digits:
        return ""
    key = normalize_phone(raw)
    if key and len(key) == 10:
        return f"+7{key}"
    if digits.startswith("8") and len(digits) == 11:
        return f"+7{digits[1:]}"
    if digits.startswith("7") and len(digits) >= 11:
        return f"+{digits}"
    if digits.startswith("00"):
        return f"+{digits[2:]}"
    if not digits.startswith("+") and len(digits) >= 10:
        return f"+{digits}"
    return f"+{digits}" if digits else ""


def cache_key_for_phone(e164: str) -> str:
    """Stable cache key — always ``+`` E.164."""
    return phone_to_e164(e164) or str(e164 or "").strip()


def map_status(raw: Any) -> tuple[bool | None, str]:
    """Return ``(active, normalized_status)``. ``None`` active = unknown.

    IRbots FAQ: ``True`` = unregistered (JSON boolean ``true`` OR string
    ``"true"``). ``session``/``used`` = has Telegram. ``ban``/``banned`` =
    blocked. Do NOT treat boolean True as «есть TG».
    """
    if raw is True:
        return False, "true"
    if raw is False:
        return False, "false"
    status = str(raw or "").strip().lower()
    if not status:
        return None, ""
    if status in _ACTIVE_STRINGS:
        return True, status
    if status in _INACTIVE_STRINGS:
        return False, status
    # Unknown vocabulary — treat as inactive but keep raw for report.
    return False, status


def status_label(*, active: bool | None, status: str) -> str:
    s = (status or "").lower()
    if active is True:
        if s in {"session", "used"}:
            return "активный (есть сессия TG)"
        return "активный"
    if s == "true" or s == "unregistered":
        return "неактивный (не зарегистрирован)"
    if s == "banned":
        return "неактивный (забанен)"
    if s == "invalid":
        return "неактивный (невалидный номер)"
    if active is False:
        return f"неактивный ({s or 'нет TG'})"
    return "не проверен"


def cached_entry(e164: str) -> dict[str, Any] | None:
    key = cache_key_for_phone(e164)
    if not key:
        return None
    entry = (load_phone_cache().get("by_phone") or {}).get(key)
    return entry if isinstance(entry, dict) else None


def upsert_phone_results(results: dict[str, dict[str, Any]]) -> int:
    """Merge phone → {status, active, checked_at, …} into durable cache."""
    if not results:
        return 0
    cache = load_phone_cache()
    by_phone = dict(cache.get("by_phone") or {})
    now = time.time()
    written = 0
    for phone, result in results.items():
        key = cache_key_for_phone(phone)
        if not key or not isinstance(result, dict):
            continue
        active, status = map_status(result.get("status", result.get("raw")))
        if "active" in result and result.get("active") is not None:
            active = bool(result.get("active"))
        if result.get("status"):
            status = str(result.get("status")).strip().lower() or status
        by_phone[key] = {
            "status": status,
            "active": active,
            "checked_at": float(result.get("checked_at") or now),
            "label": status_label(active=active, status=status),
            "source": str(result.get("source") or "irbots"),
        }
        written += 1
    stats = dict(cache.get("stats") or {})
    stats["last_run_at"] = now
    stats["phones_cached"] = len(by_phone)
    stats["active"] = sum(
        1 for v in by_phone.values() if isinstance(v, dict) and v.get("active") is True
    )
    stats["inactive"] = sum(
        1 for v in by_phone.values() if isinstance(v, dict) and v.get("active") is False
    )
    cache["by_phone"] = by_phone
    cache["stats"] = stats
    save_phone_cache(cache)
    return written


def check_numbers_remote(
    numbers: Iterable[str],
    *,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """One HTTP GET to IRbots. ``numbers`` already E.164.

    Endpoint is GET-only (POST returns 415). Keep batches ≤ ``_MAX_PER_REQUEST``
    or Apache returns 414 Request-URI Too Long.
    """
    key = api_key()
    if not key:
        return {"ok": False, "error": "IRBOTS_API_KEY missing", "data": {}}

    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in numbers:
        e164 = phone_to_e164(raw)
        if not e164 or e164 in seen:
            continue
        seen.add(e164)
        cleaned.append(e164)
    if not cleaned:
        return {"ok": True, "data": {}, "errors": 0, "status": "ok"}
    if len(cleaned) > _MAX_PER_REQUEST:
        return {
            "ok": False,
            "error": f"max {_MAX_PER_REQUEST} numbers per request",
            "data": {},
        }

    try:
        import requests
    except ImportError:
        return {"ok": False, "error": "requests not installed", "data": {}}

    params = {
        "key": key,
        "numbers": ",".join(cleaned),
        "target": "checker",
    }
    try:
        response = requests.get(api_url(), params=params, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        return {"ok": False, "error": str(exc), "data": {}}

    if not isinstance(payload, dict):
        return {"ok": False, "error": "non-json response", "data": {}}

    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    status = str(payload.get("status") or "").lower()
    if status and status not in ("ok",):
        return {
            "ok": False,
            "error": str(payload.get("msg") or status),
            "data": data,
            "status": status,
        }
    return {
        "ok": True,
        "data": data,
        "errors": int(payload.get("errors") or 0),
        "status": status or "ok",
        "time_taken": payload.get("Time taken"),
    }


def resolve_phones(
    phones: Iterable[str],
    *,
    force: bool = False,
    chunk: int = _MAX_PER_REQUEST,
) -> dict[str, dict[str, Any]]:
    """Resolve many phones using cache first; hit API only for misses.

    Returns map ``e164 → {status, active, label, cached, checked_at}``.
    """
    wanted: list[str] = []
    seen: set[str] = set()
    for raw in phones:
        e164 = phone_to_e164(raw)
        if not e164 or e164 in seen:
            continue
        seen.add(e164)
        wanted.append(e164)

    out: dict[str, dict[str, Any]] = {}
    to_fetch: list[str] = []
    cache = load_phone_cache().get("by_phone") or {}
    for e164 in wanted:
        entry = cache.get(e164) if not force else None
        if isinstance(entry, dict) and "active" in entry and not force:
            out[e164] = {
                "status": str(entry.get("status") or ""),
                "active": entry.get("active"),
                "label": str(entry.get("label") or ""),
                "checked_at": float(entry.get("checked_at") or 0),
                "cached": True,
            }
        else:
            to_fetch.append(e164)

    chunk_size = max(1, min(int(chunk or _MAX_PER_REQUEST), _MAX_PER_REQUEST))
    fresh: dict[str, dict[str, Any]] = {}
    for start in range(0, len(to_fetch), chunk_size):
        batch = to_fetch[start : start + chunk_size]
        remote = check_numbers_remote(batch)
        if not remote.get("ok"):
            log.warning("IRbots batch failed: %s", remote.get("error"))
            # Do not invent statuses; leave unchecked so a retry can fill them.
            # Also stop on hard transport errors so we don't burn credits on 414 loops.
            err = str(remote.get("error") or "")
            if "414" in err or "Request-URI Too Long" in err:
                for e164 in batch:
                    out.setdefault(
                        e164,
                        {
                            "status": "error",
                            "active": None,
                            "label": f"ошибка API: {err}",
                            "checked_at": time.time(),
                            "cached": False,
                            "error": err,
                        },
                    )
                break
            continue
        data = remote.get("data") if isinstance(remote.get("data"), dict) else {}
        # Index by normalized E.164 (API may echo with/without +).
        by_norm: dict[str, Any] = {}
        for k, v in data.items():
            by_norm[cache_key_for_phone(str(k))] = v
        now = time.time()
        for e164 in batch:
            raw_status = by_norm.get(e164)
            if raw_status is None:
                # Number missing from response — do not invent; leave unchecked.
                continue
            active, status = map_status(raw_status)
            entry = {
                "status": status,
                "active": active,
                "label": status_label(active=active, status=status),
                "checked_at": now,
                "source": "irbots",
                "raw": raw_status,
            }
            fresh[e164] = entry
            out[e164] = {**entry, "cached": False}
    if fresh:
        upsert_phone_results(fresh)
    return out


def phones_from_row(row: dict[str, Any]) -> list[str]:
    raw = str(row.get("Телефон") or row.get("phone") or "").strip()
    if not raw:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for key in all_normalized_phones(raw):
        e164 = phone_to_e164(key)
        if e164 and e164 not in seen:
            seen.add(e164)
            out.append(e164)
    if not out:
        e164 = phone_to_e164(raw)
        if e164:
            out.append(e164)
    return out


def verify_rows_via_irbots(
    rows: list[dict[str, Any]],
    *,
    only_unchecked: bool = True,
    force: bool = False,
    chunk: int = _MAX_PER_REQUEST,
) -> dict[str, Any]:
    """Check catalog rows through IRbots + phone cache; write tg_verify overlay.

    A client is **active** when any of their phones is session/used.
    Inactive when every checked phone is inactive (true/banned/invalid).
    """
    from plugins.moysklad.tg_verify import (
        overlay_for_client,
        save_verify_results_bulk,
        stamp_catalog_rows_from_verify,
    )

    stats: dict[str, Any] = {
        "rows": 0,
        "phones": 0,
        "cache_hits": 0,
        "api_fetched": 0,
        "active": 0,
        "inactive": 0,
        "skipped": 0,
        "error": None,
    }
    cache_warm = bool((load_phone_cache().get("by_phone") or {}))
    if not api_key_configured() and not cache_warm:
        stats["error"] = "IRBOTS_API_KEY missing"
        return stats
    if not api_key_configured() and force:
        stats["error"] = "IRBOTS_API_KEY missing (required for --force)"
        return stats

    by_phone_clients: dict[str, list[str]] = {}
    row_by_id: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("_moysklad_id") or row.get("id") or "").strip()
        if not cid:
            continue
        phones = phones_from_row(row)
        if not phones:
            stats["skipped"] += 1
            continue
        if only_unchecked and not force and overlay_for_client(cid):
            stats["skipped"] += 1
            continue
        row_by_id[cid] = row
        for e164 in phones:
            by_phone_clients.setdefault(e164, []).append(cid)

    stats["rows"] = len(row_by_id)
    stats["phones"] = len(by_phone_clients)
    if not by_phone_clients:
        return stats

    resolved = resolve_phones(by_phone_clients.keys(), force=force, chunk=chunk)
    stats["cache_hits"] = sum(1 for v in resolved.values() if v.get("cached"))
    stats["api_fetched"] = sum(
        1
        for v in resolved.values()
        if not v.get("cached") and v.get("active") is not None and not v.get("error")
    )

    # Per client: OR of phone actives (any session → active).
    client_phones: dict[str, list[str]] = {}
    for e164, cids in by_phone_clients.items():
        for cid in cids:
            client_phones.setdefault(cid, []).append(e164)

    results: dict[str, dict[str, Any]] = {}
    now = time.time()
    for cid, phone_list in client_phones.items():
        entries = [resolved[p] for p in phone_list if p in resolved]
        if not entries:
            continue
        # Prefer definitive actives; ignore None (API error) when others exist.
        actives = [e.get("active") for e in entries]
        if any(a is True for a in actives):
            active = True
        elif all(a is False for a in actives):
            active = False
        else:
            # Mix of errors / unknown — leave unchecked.
            continue
        best = next((e for e in entries if e.get("active") is active), entries[0])
        status = str(best.get("status") or "")
        results[cid] = {
            "active": active,
            "checked_at": float(best.get("checked_at") or now),
            "resolved_nick": normalize_tg_nick(
                (row_by_id.get(cid) or {}).get("ТГ ник")
                or (row_by_id.get(cid) or {}).get("tg_nick")
                or ""
            ),
            "via": "irbots",
            "detail": status_label(active=active, status=status),
        }

    if results:
        save_verify_results_bulk(results)
        stamp_catalog_rows_from_verify(list(row_by_id.values()))
        stats["active"] = sum(1 for r in results.values() if r.get("active"))
        stats["inactive"] = sum(1 for r in results.values() if not r.get("active"))
        stats["written"] = len(results)
    return stats


def format_row_report_line(row: dict[str, Any], entry: dict[str, Any] | None) -> str:
    """One text line: full identity + active/inactive verdict (binary only)."""
    cid = str(row.get("_moysklad_id") or row.get("id") or "").strip()
    name = str(row.get("Наименование") or row.get("name") or "").strip()
    phone = str(row.get("Телефон") or row.get("phone") or "").strip()
    nick = str(row.get("ТГ ник") or row.get("tg_nick") or "").strip()
    groups = str(row.get("Группы") or row.get("_moysklad_tags_display") or "").strip()
    if entry and "active" in entry:
        verdict = "АКТИВНЫЙ" if entry.get("active") else "НЕАКТИВНЫЙ"
        detail = str(entry.get("detail") or entry.get("label") or "")
        via = str(entry.get("via") or "")
        checked = entry.get("checked_at")
        checked_s = (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(checked)))
            if checked
            else ""
        )
    else:
        # Binary only — missing overlay → НЕАКТИВНЫЙ (never «НЕ ПРОВЕРЕН»).
        verdict = "НЕАКТИВНЫЙ"
        detail = "неактивный (нет проверки / нет телефона)"
        via = "irbots"
        checked_s = ""
    parts = [
        f"id={cid}",
        f"name={name}",
        f"phone={phone}",
        f"tg_nick={nick}",
        f"groups={groups}",
        f"status={verdict}",
    ]
    if detail:
        parts.append(f"detail={detail}")
    if via:
        parts.append(f"via={via}")
    if checked_s:
        parts.append(f"checked_at={checked_s}")
    return " | ".join(parts)


def force_complete_unchecked_rows(
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Mark every client without overlay as НЕАКТИВНЫЙ (no third status).

    No-phone / never-checked → inactive. Then rewrite report + stamp catalog.
    Refuses to run (and never wipes overlay) when catalog rows are empty.
    """
    from plugins.moysklad.tg_verify import (
        load_overlay,
        overlay_for_client,
        persist_verify_into_catalog,
        save_verify_results_bulk,
        stamp_catalog_rows_from_verify,
    )

    if rows is None:
        rows = _load_catalog_rows()

    if not rows:
        return {
            "ok": False,
            "error": "no catalog rows — refuse wipe",
            "forced": 0,
            "written": 0,
        }

    overlay = load_overlay()
    by_id = overlay.get("by_client_id") if isinstance(overlay, dict) else {}
    if not isinstance(by_id, dict):
        by_id = {}

    # Seed overlay from already-stamped catalog / phone cache when overlay empty.
    seeded = _seed_overlay_from_rows(rows, by_id)
    if seeded:
        save_verify_results_bulk(seeded)
        by_id = load_overlay().get("by_client_id") or {}
        if not isinstance(by_id, dict):
            by_id = {}

    results: dict[str, dict[str, Any]] = {}
    now = time.time()
    for row in rows:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("_moysklad_id") or row.get("id") or "").strip()
        if not cid:
            continue
        existing = by_id.get(cid)
        if not isinstance(existing, dict):
            existing = overlay_for_client(cid)
        if isinstance(existing, dict) and "active" in existing:
            continue
        phones = phones_from_row(row)
        detail = (
            "неактивный (нет TG / не найден)"
            if phones
            else "неактивный (нет телефона)"
        )
        results[cid] = {
            "active": False,
            "checked_at": now,
            "resolved_nick": "",
            "chat_id": "",
            "via": "irbots",
            "detail": detail,
        }

    written = 0
    if results:
        written = save_verify_results_bulk(results)

    stamp_catalog_rows_from_verify(list(rows))
    report = write_full_report(list(rows))
    dest = _copy_report_beside_repo(report)
    persisted = persist_verify_into_catalog(list(rows))
    return {
        "ok": True,
        "forced": len(results),
        "seeded": len(seeded),
        "written": written,
        "report": str(report),
        "report_copy": str(dest) if dest else "",
        "catalog": persisted,
        "clients": len(rows),
    }


def _load_catalog_rows() -> list[dict[str, Any]]:
    try:
        from plugins.moysklad.catalog_cache import get_cached, cache_key

        env = get_cached(cache_key())
        catalog = env.get("catalog") if isinstance(env, dict) else None
        rows = list((catalog or {}).get("rows") or [])
        if rows:
            return rows
    except Exception:
        pass
    # Fallback: largest on-disk catalog under HERMES_HOME/moysklad/cache.
    root = get_hermes_home() / "moysklad" / "cache"
    if not root.is_dir():
        return []
    best: list[dict[str, Any]] = []
    for path in root.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        cat = data.get("catalog") if isinstance(data, dict) else None
        rows = list((cat or {}).get("rows") or []) if isinstance(cat, dict) else []
        if len(rows) > len(best):
            best = rows
    return best


def _seed_overlay_from_rows(
    rows: list[dict[str, Any]],
    by_id: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Rebuild missing overlay entries from stamped row.tg_active + phone cache."""
    phone_cache = load_phone_cache().get("by_phone") or {}
    seeded: dict[str, dict[str, Any]] = {}
    now = time.time()
    for row in rows:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("_moysklad_id") or row.get("id") or "").strip()
        if not cid or (isinstance(by_id.get(cid), dict) and "active" in (by_id.get(cid) or {})):
            continue
        active = row.get("tg_active")
        detail = str(row.get("tg_active_detail") or row.get("tg_active_label") or "").strip()
        via = str(row.get("tg_active_via") or "irbots").strip() or "irbots"
        if active is not True and active is not False:
            # Try phone cache before leaving for force-inactive pass.
            for e164 in phones_from_row(row):
                key = cache_key_for_phone(e164)
                entry = phone_cache.get(key) if isinstance(phone_cache, dict) else None
                if isinstance(entry, dict) and "active" in entry:
                    active = bool(entry.get("active"))
                    detail = str(entry.get("label") or entry.get("detail") or detail)
                    via = "irbots"
                    break
        if active is not True and active is not False:
            continue
        seeded[cid] = {
            "active": bool(active),
            "checked_at": float(row.get("tg_active_checked_at") or now),
            "resolved_nick": str(row.get("tg_active_nick") or ""),
            "chat_id": str(row.get("tg_chat_id") or ""),
            "via": via,
            "detail": detail
            or (
                "активный (есть сессия TG)"
                if active
                else "неактивный (нет TG / не найден)"
            ),
        }
    return seeded


def _copy_report_beside_repo(report: Path) -> Path | None:
    try:
        data_dir = Path(__file__).resolve().parents[2] / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        dest = data_dir / "irbots_clients_status.txt"
        dest.write_text(report.read_text(encoding="utf-8"), encoding="utf-8")
        return dest
    except Exception:
        return None


def write_full_report(
    rows: list[dict[str, Any]],
    path: Path | None = None,
) -> Path:
    """Write every client row + TG verdict to a UTF-8 text file."""
    from plugins.moysklad.tg_verify import overlay_for_client, load_overlay

    out = path or (get_hermes_home() / "moysklad" / "irbots_clients_status.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    overlay = load_overlay()
    by_id = overlay.get("by_client_id") or {}
    phone_cache = load_phone_cache().get("by_phone") or {}
    lines: list[str] = [
        f"# IRbots TG status report — {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"# clients={len(rows)} overlay={len(by_id)} phones_cached={len(phone_cache)}",
        "",
    ]
    active = inactive = 0
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("_moysklad_id") or row.get("id") or "").strip()
        entry = by_id.get(cid) if cid else None
        if not isinstance(entry, dict):
            entry = overlay_for_client(cid) if cid else None
        # Always binary: missing entry renders as НЕАКТИВНЫЙ in the line.
        lines.append(format_row_report_line(row, entry if isinstance(entry, dict) else None))
        if isinstance(entry, dict) and entry.get("active") is True:
            active += 1
        else:
            inactive += 1
    lines[1] = (
        f"# clients={len(rows)} active={active} inactive={inactive} "
        f"unchecked=0 phones_cached={len(phone_cache)}"
    )
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def parse_status_report(path: Path) -> dict[str, dict[str, Any]]:
    """Parse ``irbots_clients_status.txt`` → client_id → overlay entry."""
    text = path.read_text(encoding="utf-8")
    out: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        if not line.startswith("id="):
            continue
        parts = dict(p.split("=", 1) for p in line.split(" | ") if "=" in p)
        cid = str(parts.get("id") or "").strip()
        if not cid:
            continue
        status = str(parts.get("status") or "").strip().upper()
        if status == "АКТИВНЫЙ":
            active: bool | None = True
        elif status == "НЕАКТИВНЫЙ":
            active = False
        else:
            continue  # НЕ ПРОВЕРЕН — leave unchecked
        checked_raw = str(parts.get("checked_at") or "").strip()
        checked_at = time.time()
        if checked_raw:
            try:
                checked_at = time.mktime(
                    time.strptime(checked_raw, "%Y-%m-%d %H:%M:%S")
                )
            except ValueError:
                pass
        out[cid] = {
            "active": active,
            "checked_at": checked_at,
            "resolved_nick": "",
            "chat_id": "",
            "via": str(parts.get("via") or "irbots").strip() or "irbots",
            "detail": str(parts.get("detail") or "").strip(),
        }
    return out


def apply_status_report_file(
    path: Path | None = None,
    *,
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Force overlay + catalog from the status text file (source of truth).

    Wipes AI «новый» status pollution: clears page snapshots, strips ``state``
    from ai_fill cache, stamps catalog, rewrites durable cache.
    """
    from plugins.moysklad.tg_verify import (
        persist_verify_into_catalog,
        save_verify_results_bulk,
        save_overlay,
        _empty_overlay,
    )

    report = path or (get_hermes_home() / "moysklad" / "irbots_clients_status.txt")
    if not report.is_file():
        alt = Path(__file__).resolve().parents[2] / "data" / "irbots_clients_status.txt"
        if alt.is_file():
            report = alt
    if not report.is_file():
        return {"ok": False, "error": f"missing report: {report}"}

    parsed = parse_status_report(report)
    # Replace overlay entirely with file contents.
    save_overlay(_empty_overlay())
    written = save_verify_results_bulk(parsed)

    stripped_ai = _strip_ai_fill_state_fields()
    persisted = persist_verify_into_catalog(rows)
    return {
        "ok": True,
        "report": str(report),
        "parsed": len(parsed),
        "written": written,
        "ai_state_stripped": stripped_ai,
        "catalog": persisted,
    }


def _strip_ai_fill_state_fields() -> dict[str, int]:
    """Remove legacy AI ``state`` / «новый» from ai_fill cache entries."""
    try:
        from plugins.moysklad import ai_fill
    except Exception:
        return {"entries": 0, "stripped": 0}

    stripped = 0
    entries = 0
    root = get_hermes_home() / "moysklad" / "ai_fill_cache"
    if root.is_dir():
        for path in root.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            entries += 1
            fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
            ai_fields = list(data.get("ai_fields") or [])
            changed = False
            if "state" in fields:
                fields.pop("state", None)
                changed = True
            if "state" in ai_fields:
                ai_fields = [f for f in ai_fields if f != "state"]
                changed = True
            if changed:
                data["fields"] = fields
                data["ai_fields"] = ai_fields
                path.write_text(
                    json.dumps(data, ensure_ascii=False, default=str),
                    encoding="utf-8",
                )
                stripped += 1
                cid = str(data.get("client_id") or path.stem)
                # Drop memory copy if present.
                try:
                    ai_fill._MEMORY.pop(cid, None)
                except Exception:
                    pass

    legacy = get_hermes_home() / "moysklad" / "ai_fill.json"
    if legacy.is_file():
        try:
            blob = json.loads(legacy.read_text(encoding="utf-8"))
            by = blob.get("by_client_id") if isinstance(blob, dict) else None
            if isinstance(by, dict):
                for cid, entry in list(by.items()):
                    if not isinstance(entry, dict):
                        continue
                    entries += 1
                    fields = entry.get("fields") if isinstance(entry.get("fields"), dict) else {}
                    ai_fields = list(entry.get("ai_fields") or [])
                    if "state" in fields or "state" in ai_fields:
                        fields.pop("state", None)
                        entry["fields"] = fields
                        entry["ai_fields"] = [f for f in ai_fields if f != "state"]
                        by[cid] = entry
                        stripped += 1
                blob["by_client_id"] = by
                legacy.write_text(
                    json.dumps(blob, ensure_ascii=False, default=str),
                    encoding="utf-8",
                )
        except Exception:
            pass

    # Reset in-process AI fill memory so apply_ai_fill can't resurrect «новый».
    try:
        ai_fill._MEMORY.clear()
    except Exception:
        pass
    return {"entries": entries, "stripped": stripped}
