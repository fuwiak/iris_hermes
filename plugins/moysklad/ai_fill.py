"""AI fill for empty MoySklad CRM fields (Группы, Статус, Пол, …).

Mirrors client_segmentation_deepseek: only fill blank cells, stamp
``ai_fields`` so the UI can draw green AI markers. Never overwrites
MoySklad-owned non-empty cells.

Persistence ladder (same idea as outreach/catalog cache):

1. Redis — when ``REDIS_URL`` / ``MOYSKLAD_REDIS_URL`` is set
2. Per-client JSON under ``$HERMES_HOME/moysklad/ai_fill_cache/``
3. Legacy bulk ``$HERMES_HOME/moysklad/ai_fill.json`` (read + migrate)
4. Process-local memory

Lazy UI fills only the visible page; cached entries skip LLM on reload.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from hermes_constants import get_hermes_home
from plugins.moysklad.assign_groups import heuristic_groups_for_row, merge_tags
from plugins.moysklad.sales_channels import (
    SALES_CHANNEL_TYPE_DIRECT,
    SALES_CHANNEL_TYPE_HYBRID,
    is_direct_sales_channel,
    moysklad_group_tokens,
    sales_channel_type_from_channels,
)

log = logging.getLogger(__name__)

_LOCK = threading.RLock()
_MEMORY: dict[str, dict[str, Any]] = {}
DEFAULT_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days

# Public API key → (row column candidates, human label for LLM)
_FILLABLE: dict[str, tuple[tuple[str, ...], str]] = {
    "groups": (("Группы", "_moysklad_tags"), "Группы"),
    "state": (("_moysklad_state", "Статус"), "Статус"),
    "sex": (("Пол",), "Пол"),
    "role": (("Заказчик или получатель",), "Заказчик или получатель"),
    "tg_nick": (("ТГ ник",), "ТГ ник"),
    "company_type": (("Тип контрагента",), "Тип контрагента"),
}

_AI_NO_DATA = frozenset(
    {"", "—", "-", "нет", "none", "n/a", "null", "не найдено", "unknown"}
)

_FEMALE_NAME_HINTS = (
    "а",
    "я",
    "ия",
    "ья",
    "на",
    "ла",
    "ра",
    "са",
    "та",
    "да",
    "ва",
    "га",
    "жа",
    "за",
    "ка",
    "ма",
    "па",
    "фа",
    "ха",
    "ца",
    "ча",
    "ша",
    "ща",
)
_MALE_EXPLICIT = frozenset(
    {
        "александр",
        "алексей",
        "андрей",
        "артём",
        "артем",
        "борис",
        "вадим",
        "василий",
        "виктор",
        "владимир",
        "владислав",
        "глеб",
        "григорий",
        "денис",
        "дмитрий",
        "евгений",
        "игорь",
        "илья",
        "кирилл",
        "константин",
        "леонид",
        "максим",
        "михаил",
        "николай",
        "олег",
        "павел",
        "пётр",
        "петр",
        "роман",
        "сергей",
        "станислав",
        "степан",
        "тимофей",
        "фёдор",
        "федор",
        "юрий",
        "ярослав",
    }
)
_FEMALE_EXPLICIT = frozenset(
    {
        "александра",
        "алёна",
        "алена",
        "анастасия",
        "анна",
        "валентина",
        "вера",
        "виктория",
        "галина",
        "дарья",
        "екатерина",
        "елена",
        "ирина",
        "ксения",
        "людмила",
        "мария",
        "марина",
        "наталья",
        "ольга",
        "полина",
        "светлана",
        "татьяна",
        "юлия",
    }
)

_SYSTEM = """Ты — аналитик CRM цветочного магазина (МойСклад).
Заполни ТОЛЬКО пустые поля клиента. Не выдумывай телефоны, email, адреса, заказы.
Отвечай строго JSON без markdown:
{"results":[{"id":"...","Группы":["тег1","тег2"],"Статус":"...","Пол":"Мужской|Женский","Заказчик или получатель":"...","ТГ ник":"@nick или пусто","Тип контрагента":"..."}]}
Правила:
- Группы: короткие теги (премиум, постоянный клиент, новый, событие марта, 8 марта, маркетплейс, прямые продажи…)
- «прямые продажи» ставь ТОЛЬКО если в заказах есть реальный канал продаж Telegram / WhatsApp/MAX / Витрина / сайт.
  Группа МойСклада «WhatsApp» / «watsapp» / «Telegram» — это способ связи, НЕ канал продаж.
  Если заказы только с маркетплейсов (FlowWow, Ozon, WB и т.п.) — ставь «маркетплейс», не «прямые продажи».
- Статус: активный / спящий / новый / архивный — по заказам
- Пол: только если ясно из имени; иначе опусти поле
- ТГ ник: только если есть в данных; иначе опусти
- Не заполняй поля, которые уже не пустые в current
"""


def _legacy_store_path() -> Path:
    root = get_hermes_home() / "moysklad"
    root.mkdir(parents=True, exist_ok=True)
    return root / "ai_fill.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def cache_ttl_seconds() -> int:
    raw = (os.environ.get("MOYSKLAD_AI_FILL_TTL_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_TTL_SECONDS
    try:
        return max(3600, int(raw))
    except ValueError:
        return DEFAULT_TTL_SECONDS


def _redis_url() -> str:
    return (os.environ.get("REDIS_URL") or os.environ.get("MOYSKLAD_REDIS_URL") or "").strip()


def _account_fingerprint() -> str:
    token = (os.environ.get("MOYSKLAD_API_TOKEN") or "").strip()
    if not token:
        return "no-token"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def fill_cache_key(client_id: str) -> str:
    cid = (client_id or "").strip()
    return f"moysklad:ai-fill:v1:{_account_fingerprint()}:{cid}"


def _file_path(key: str) -> Path:
    safe = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    root = get_hermes_home() / "moysklad" / "ai_fill_cache"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{safe}.json"


def _redis_client():
    url = _redis_url()
    if not url:
        return None
    try:
        import redis  # type: ignore[import-not-found]
    except Exception:
        log.debug("REDIS_URL set but redis package missing; ai_fill file cache")
        return None
    try:
        client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2.0)
        client.ping()
        return client
    except Exception as exc:
        log.warning("MoySklad ai_fill Redis unavailable (%s); file cache", exc)
        return None


def cache_backend_name() -> str:
    if _redis_client() is not None:
        return "redis+file"
    return "file"


def clear_memory_for_tests() -> None:
    with _LOCK:
        _MEMORY.clear()


def _envelope(entry: dict[str, Any], *, saved_at: float) -> dict[str, Any]:
    return {
        "saved_at": float(saved_at),
        "ttl_seconds": cache_ttl_seconds(),
        "entry": entry,
    }


def _is_fresh(envelope: dict[str, Any], *, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    saved_at = float(envelope.get("saved_at") or 0)
    ttl = int(envelope.get("ttl_seconds") or cache_ttl_seconds())
    return saved_at > 0 and (now - saved_at) < ttl


def _load_legacy_entry(client_id: str) -> Optional[dict[str, Any]]:
    path = _legacy_store_path()
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    entry = raw.get(client_id)
    return dict(entry) if isinstance(entry, dict) else None


def get_ai_fill_entry(client_id: str) -> Optional[dict[str, Any]]:
    """Return persisted fill entry for one client, or None."""
    cid = (client_id or "").strip()
    if not cid:
        return None
    key = fill_cache_key(cid)
    now = time.time()
    with _LOCK:
        mem = _MEMORY.get(key)
        if mem and _is_fresh(mem, now=now):
            entry = mem.get("entry")
            return dict(entry) if isinstance(entry, dict) else None

    client = _redis_client()
    if client is not None:
        try:
            raw = client.get(key)
            if raw:
                envelope = json.loads(raw)
                if isinstance(envelope, dict) and _is_fresh(envelope, now=now):
                    with _LOCK:
                        _MEMORY[key] = envelope
                    entry = envelope.get("entry")
                    return dict(entry) if isinstance(entry, dict) else None
        except Exception as exc:
            log.warning("MoySklad ai_fill Redis get failed: %s", exc)

    path = _file_path(key)
    try:
        if path.is_file():
            envelope = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(envelope, dict) and _is_fresh(envelope, now=now):
                with _LOCK:
                    _MEMORY[key] = envelope
                entry = envelope.get("entry")
                return dict(entry) if isinstance(entry, dict) else None
    except Exception as exc:
        log.warning("MoySklad ai_fill file cache read failed: %s", exc)

    legacy = _load_legacy_entry(cid)
    if legacy:
        set_ai_fill_entry(cid, legacy)
        return legacy
    return None


def set_ai_fill_entry(
    client_id: str,
    entry: dict[str, Any],
    *,
    saved_at: float | None = None,
) -> dict[str, Any]:
    """Persist one client's AI fill entry; return envelope."""
    cid = (client_id or "").strip()
    if not cid:
        raise ValueError("client_id required")
    payload = {
        "fields": dict(entry.get("fields") or {}),
        "ai_fields": list(entry.get("ai_fields") or []),
        "attempted_keys": list(entry.get("attempted_keys") or []),
        "source": str(entry.get("source") or ""),
        "updated_at": str(entry.get("updated_at") or _now()),
    }
    key = fill_cache_key(cid)
    envelope = _envelope(payload, saved_at=saved_at or time.time())
    ttl = int(envelope["ttl_seconds"])

    with _LOCK:
        _MEMORY[key] = envelope

    client = _redis_client()
    if client is not None:
        try:
            client.setex(key, ttl, json.dumps(envelope, ensure_ascii=False, default=str))
        except Exception as exc:
            log.warning("MoySklad ai_fill Redis set failed: %s", exc)

    path = _file_path(key)
    try:
        path.write_text(
            json.dumps(envelope, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception as exc:
        log.warning("MoySklad ai_fill file cache write failed: %s", exc)

    # Keep legacy bulk file in sync for older readers / ops tooling.
    try:
        legacy_path = _legacy_store_path()
        with _LOCK:
            store: dict[str, Any] = {}
            if legacy_path.is_file():
                try:
                    raw = json.loads(legacy_path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        store = raw
                except (OSError, json.JSONDecodeError):
                    store = {}
            store[cid] = payload
            tmp = legacy_path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(store, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            tmp.replace(legacy_path)
    except Exception as exc:
        log.debug("MoySklad ai_fill legacy sync skipped: %s", exc)

    return envelope


def is_empty_cell(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (list, tuple, set)):
        return not any(str(v).strip() for v in value)
    text = str(value).strip()
    return not text or text.lower() in _AI_NO_DATA


def _row_value(row: dict[str, Any], public_key: str) -> Any:
    cols, _label = _FILLABLE[public_key]
    if public_key == "groups":
        tags = row.get("_moysklad_tags") or moysklad_group_tokens(row) or []
        groups = row.get("Группы") or ""
        if tags:
            return tags
        return groups
    for col in cols:
        if col in row and not is_empty_cell(row.get(col)):
            return row.get(col)
    return row.get(cols[0]) if cols else None


def empty_fillable_keys(row: dict[str, Any]) -> list[str]:
    return [key for key in _FILLABLE if is_empty_cell(_row_value(row, key))]


def _guess_sex_from_name(name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        return ""
    # Take first token that looks like a given name
    token = re.split(r"[\s,./]+", raw)[0].lower().replace("ё", "е")
    if token in _MALE_EXPLICIT:
        return "Мужской"
    if token in _FEMALE_EXPLICIT:
        return "Женский"
    if len(token) >= 3 and token.endswith(_FEMALE_NAME_HINTS):
        # Avoid male names ending with а that aren't listed (редко)
        if token in {"никита", "илья", "кузьма", "фома", "савва"}:
            return "Мужской"
        return "Женский"
    if len(token) >= 3:
        return "Мужской"
    return ""


def _guess_state(row: dict[str, Any]) -> str:
    from plugins.moysklad.order_status import summarize_order_context

    ctx = row.get("_orders_context") if isinstance(row.get("_orders_context"), list) else []
    payment = summarize_order_context(ctx)
    fulfilled = int(
        row.get("fulfilled_order_count")
        or payment.get("fulfilled_order_count")
        or 0
    )
    total = int(
        row.get("order_count")
        or row.get("Всего заказов")
        or payment.get("order_count")
        or 0
    )
    unpaid_n = int(payment.get("unpaid_order_count") or 0)
    cancelled_n = int(payment.get("cancelled_order_count") or 0)
    if payment.get("failed_only") or (total > 0 and fulfilled <= 0 and (unpaid_n + cancelled_n) > 0):
        return "несостоявшийся"
    last = str(
        row.get("last_order_at")
        or row.get("Дата последнего заказа")
        or payment.get("last_paid_order_at")
        or ""
    )
    if fulfilled <= 1:
        return "новый"
    year_m = re.search(r"(20\d{2})", last)
    if year_m:
        try:
            year = int(year_m.group(1))
            now_year = datetime.now(timezone.utc).year
            if now_year - year >= 2:
                return "спящий"
        except ValueError:
            pass
    return "активный"


def _tg_from_conversation(row: dict[str, Any]) -> str:
    conv = str(row.get("TG conversation") or row.get("tg_conversation") or "").strip()
    m = re.search(r"(?:t\.me|telegram\.me)/([A-Za-z0-9_]{4,64})", conv, re.I)
    if m:
        return f"@{m.group(1)}"
    nick = str(row.get("ТГ ник") or "").strip()
    return nick


def _order_channel_labels(row: dict[str, Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for order in row.get("_orders_context") or []:
        if not isinstance(order, dict):
            continue
        ch = str(order.get("Канал продаж") or order.get("channel") or "").strip()
        key = ch.lower()
        if not ch or key in seen:
            continue
        seen.add(key)
        out.append(ch)
    return out


def sanitize_sales_type_groups(row: dict[str, Any], groups: list[str]) -> list[str]:
    """Drop AI «прямые продажи» when orders have no real direct sales channel.

    WhatsApp/Telegram MoySklad *groups* are contact methods — they must not
    justify a direct-sales label for marketplace-only clients.
    """
    order_channels = _order_channel_labels(row)
    has_direct_order = any(is_direct_sales_channel(c) for c in order_channels)
    sales_type = sales_channel_type_from_channels(order_channels)
    out: list[str] = []
    seen: set[str] = set()
    for raw in groups or []:
        name = str(raw or "").strip()
        key = name.lower().replace("ё", "е")
        if not name or key in seen:
            continue
        if key in ("прямые продажи", "прямые") and not has_direct_order:
            continue
        if key in ("прямые продажи", "прямые") and sales_type not in (
            SALES_CHANNEL_TYPE_DIRECT,
            SALES_CHANNEL_TYPE_HYBRID,
        ):
            continue
        seen.add(key)
        out.append(name)
    return out


def heuristic_fill_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return public-key → value for empty fields only (heuristic)."""
    empty = set(empty_fillable_keys(row))
    out: dict[str, Any] = {}
    if "groups" in empty:
        existing = list(row.get("_moysklad_tags") or moysklad_group_tokens(row) or [])
        proposed = heuristic_groups_for_row(row)
        merged = merge_tags(existing, proposed)
        if merged and is_empty_cell(existing):
            out["groups"] = sanitize_sales_type_groups(row, merged)
        elif proposed and is_empty_cell(existing):
            out["groups"] = sanitize_sales_type_groups(row, list(proposed))
    if "state" in empty:
        out["state"] = _guess_state(row)
    if "sex" in empty:
        sex = _guess_sex_from_name(str(row.get("Наименование") or row.get("name") or ""))
        if sex:
            out["sex"] = sex
    if "role" in empty:
        # Prefer recipient when delivery address / comment hints a gift receiver.
        blob = " ".join(
            str(x or "")
            for x in (
                row.get("description"),
                row.get("_comment_blob"),
                row.get("Фактический адрес (Комментарий)"),
                row.get("actual_address_comment"),
                row.get("Наименование"),
            )
        ).lower()
        if any(
            tip in blob
            for tip in (
                "получател",
                "доставит",
                "доставка для",
                "подарок",
                "сюрприз",
                "адресат",
            )
        ):
            out["role"] = "получатель"
        else:
            out["role"] = "заказчик"
    if "tg_nick" in empty:
        nick = _tg_from_conversation(row)
        if nick:
            out["tg_nick"] = nick
    if "company_type" in empty:
        name = str(row.get("Наименование") or "").lower()
        if any(x in name for x in ("ооо", "ип ", "ао ", "зао", "пао")):
            out["company_type"] = "юрлицо"
        else:
            out["company_type"] = "физлицо"
    return {k: v for k, v in out.items() if not is_empty_cell(v)}


def _parse_llm_results(text: str) -> list[dict[str, Any]]:
    raw = (text or "").strip()
    if not raw:
        return []
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            raw = "\n".join(
                lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            ).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
    if isinstance(data, dict):
        items = data.get("results") or data.get("clients") or []
    elif isinstance(data, list):
        items = data
    else:
        items = []
    return [i for i in items if isinstance(i, dict)]


def _llm_fill_batch(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """id → public-key fills from LLM. Empty dict on failure."""
    payload = []
    for row in rows:
        empty = empty_fillable_keys(row)
        if not empty:
            continue
        cid = str(row.get("_moysklad_id") or row.get("id") or "").strip()
        if not cid:
            continue
        current = { _FILLABLE[k][1]: _row_value(row, k) for k in _FILLABLE }
        payload.append({
            "id": cid,
            "empty_fields": [_FILLABLE[k][1] for k in empty],
            "current": current,
            "name": row.get("Наименование") or row.get("name") or "",
            "order_count": row.get("order_count") or row.get("Всего заказов") or 0,
            "avg_check": row.get("avg_check") or row.get("Средний чек") or 0,
            "last_order_at": row.get("last_order_at")
            or row.get("Дата последнего заказа")
            or "",
            "channels": row.get("Канал продаж") or row.get("channels") or "",
            "tags": list(row.get("_moysklad_tags") or [])[:12],
        })
    if not payload:
        return {}
    try:
        from agent.auxiliary_client import call_llm, extract_content_or_reasoning

        response = call_llm(
            task="compression",
            messages=[
                {"role": "system", "content": _SYSTEM},
                {
                    "role": "user",
                    "content": json.dumps({"clients": payload}, ensure_ascii=False),
                },
            ],
            max_tokens=1800,
            temperature=0.2,
            timeout=60.0,
        )
        text = (extract_content_or_reasoning(response) or "").strip()
        items = _parse_llm_results(text)
    except Exception as exc:
        log.warning("moysklad ai_fill LLM unavailable: %s", exc)
        return {}

    label_to_key = {v[1]: k for k, v in _FILLABLE.items()}
    out: dict[str, dict[str, Any]] = {}
    for item in items:
        cid = str(item.get("id") or "").strip()
        if not cid:
            continue
        fills: dict[str, Any] = {}
        for label, key in label_to_key.items():
            if label not in item and key not in item:
                continue
            raw_val = item.get(label, item.get(key))
            if is_empty_cell(raw_val):
                continue
            if key == "groups":
                if isinstance(raw_val, str):
                    parts = [p.strip() for p in re.split(r"[,;|/]", raw_val) if p.strip()]
                elif isinstance(raw_val, list):
                    parts = [str(p).strip() for p in raw_val if str(p).strip()]
                else:
                    parts = []
                if parts:
                    fills["groups"] = parts
            elif key == "sex":
                s = str(raw_val).strip().lower()
                if s.startswith("муж"):
                    fills["sex"] = "Мужской"
                elif s.startswith("жен"):
                    fills["sex"] = "Женский"
            else:
                fills[key] = str(raw_val).strip()
        if fills:
            out[cid] = fills
    return out


def _persist_fills(
    client_id: str,
    fills: dict[str, Any],
    *,
    source: str,
    attempted_keys: list[str] | None = None,
) -> dict[str, Any]:
    prev = get_ai_fill_entry(client_id) or {}
    fields = dict(prev.get("fields") or {})
    ai_fields = list(prev.get("ai_fields") or [])
    attempted = list(prev.get("attempted_keys") or [])
    for key, value in fills.items():
        fields[key] = value
        if key not in ai_fields:
            ai_fields.append(key)
    for key in attempted_keys or []:
        if key not in attempted:
            attempted.append(key)
    entry = {
        "fields": fields,
        "ai_fields": ai_fields,
        "attempted_keys": attempted,
        "source": source,
        "updated_at": _now(),
    }
    set_ai_fill_entry(client_id, entry)
    return {"client_id": client_id, **entry}


def apply_ai_fill_to_public(client: dict[str, Any]) -> dict[str, Any]:
    """Merge persisted AI fills into a public client dict.

    Empty slots (state/sex/role/tg/…) get AI values. Groups are enriched:
    MoySklad tags stay, AI labels append (deduped) and are exposed as
    ``ai_groups`` so the UI can mark МС vs AI.
    """
    cid = str(client.get("id") or "").strip()
    if not cid:
        client.setdefault("ai_fields", [])
        client.setdefault("ai_groups", [])
        return client
    entry = get_ai_fill_entry(cid)
    if not isinstance(entry, dict):
        # Still surface AI overlay groups from store helper when present
        try:
            ai_only = ai_group_labels_for_client(cid)
        except Exception:
            ai_only = []
        if ai_only:
            return _merge_ai_groups_into_public(dict(client), ai_only)
        client.setdefault("ai_fields", [])
        client.setdefault("ai_groups", [])
        return client
    fields = entry.get("fields") or {}
    ai_fields: list[str] = []
    out = dict(client)
    out.setdefault("ms_groups", out.get("groups") or "")
    for key in _FILLABLE:
        if key not in fields:
            continue
        value = fields[key]
        if key == "groups":
            if isinstance(value, list):
                ai_list = [str(v).strip() for v in value if str(v).strip()]
            elif isinstance(value, str) and value.strip():
                ai_list = [p.strip() for p in re.split(r"[,;|/]", value) if p.strip()]
            else:
                ai_list = []
            out = _merge_ai_groups_into_public(out, ai_list)
            if ai_list:
                ai_fields.append("groups")
            continue
        if not is_empty_cell(out.get(key)):
            continue
        out[key] = value
        ai_fields.append(key)
    # Keep stamp even if MoySklad later filled — only show green on currently AI-shown
    out["ai_fields"] = ai_fields
    out["ai_fill_source"] = entry.get("source") or ""
    out["ai_fill_cached"] = True
    out.setdefault("ai_groups", [])
    return out


def _merge_ai_groups_into_public(client: dict[str, Any], ai_list: list[str]) -> dict[str, Any]:
    """Append AI group labels onto MoySklad groups without dropping MS tags."""
    out = dict(client)
    ms_raw = str(out.get("ms_groups") or out.get("groups") or "").strip()
    ms_parts = [p.strip() for p in re.split(r"[,;|/]", ms_raw) if p.strip()]
    if not ms_parts:
        ms_parts = [str(t).strip() for t in (out.get("tags") or []) if str(t).strip()]
    seen = {p.lower() for p in ms_parts}
    ai_clean: list[str] = []
    for name in ai_list:
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        ai_clean.append(name)
    merged = ms_parts + ai_clean
    out["ms_groups"] = ", ".join(ms_parts)
    out["ai_groups"] = ai_clean
    out["groups"] = ", ".join(merged)
    out["tags"] = list(merged)
    return out


def fill_empty_for_rows(
    rows: list[dict[str, Any]],
    *,
    client_ids: list[str] | None = None,
    limit: int = 40,
    use_llm: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    """Fill empty fields for matching rows. Returns summary + per-client results.

    Cached Redis/file entries skip LLM unless ``force=True``. Pass ``client_ids``
    for lazy evaluation of the currently visible page.
    """
    id_filter = {str(i).strip() for i in (client_ids or []) if str(i).strip()}
    targets: list[dict[str, Any]] = []
    cached_hits: list[dict[str, Any]] = []
    for row in rows:
        cid = str(row.get("_moysklad_id") or row.get("id") or "").strip()
        if not cid:
            continue
        if id_filter and cid not in id_filter:
            continue
        empty = empty_fillable_keys(row)
        if not empty:
            continue
        existing = None if force else get_ai_fill_entry(cid)
        if existing:
            attempted = set(existing.get("attempted_keys") or [])
            fields = existing.get("fields") or {}
            still_need = [k for k in empty if k not in fields and k not in attempted]
            if not still_need:
                cached_hits.append({
                    "id": cid,
                    "name": row.get("Наименование") or row.get("name") or "",
                    "filled": {},
                    "ai_fields": list(existing.get("ai_fields") or []),
                    "fields": fields,
                    "source": existing.get("source") or "cache",
                    "from_cache": True,
                    "empty_before": sorted(empty),
                })
                continue
        targets.append(row)
        # Lazy page fills can request up to ~200 visible rows; keep a hard cap.
        cap = 200 if id_filter else 100
        if len(targets) >= max(1, min(int(limit), cap)):
            break

    llm_map: dict[str, dict[str, Any]] = {}
    source = "heuristic"
    if use_llm and targets:
        llm_map = _llm_fill_batch(targets)
        if llm_map:
            source = "llm"

    results = list(cached_hits)
    filled_fields = 0
    for row in targets:
        cid = str(row.get("_moysklad_id") or row.get("id") or "").strip()
        empty = set(empty_fillable_keys(row))
        heur = heuristic_fill_row(row)
        llm = {k: v for k, v in (llm_map.get(cid) or {}).items() if k in empty}
        # LLM wins when present; heuristic fills the rest
        merged = dict(heur)
        merged.update(llm)
        if "groups" in merged and isinstance(merged["groups"], list):
            merged["groups"] = sanitize_sales_type_groups(row, list(merged["groups"]))
        merged = {k: v for k, v in merged.items() if k in empty and not is_empty_cell(v)}
        row_source = "llm" if any(k in llm for k in merged) else "heuristic"
        if llm and heur and any(k not in llm for k in merged):
            row_source = "llm+heuristic"
        entry = _persist_fills(
            cid,
            merged,
            source=row_source,
            attempted_keys=sorted(empty),
        )
        filled_fields += len(merged)
        results.append({
            "id": cid,
            "name": row.get("Наименование") or row.get("name") or "",
            "filled": merged,
            "ai_fields": entry.get("ai_fields") or [],
            "fields": entry.get("fields") or {},
            "source": row_source,
            "from_cache": False,
            "empty_before": sorted(empty),
        })

    return {
        "ok": True,
        "source": source if targets else ("cache" if cached_hits else source),
        "cache_backend": cache_backend_name(),
        "scanned": len(targets) + len(cached_hits),
        "updated": len([r for r in results if not r.get("from_cache")]),
        "cached": len(cached_hits),
        "filled_field_count": filled_fields,
        "results": results,
    }


def clear_ai_fill(client_id: str = "") -> dict[str, Any]:
    cid = (client_id or "").strip()
    if cid:
        key = fill_cache_key(cid)
        with _LOCK:
            _MEMORY.pop(key, None)
        client = _redis_client()
        if client is not None:
            try:
                client.delete(key)
            except Exception as exc:
                log.warning("MoySklad ai_fill Redis delete failed: %s", exc)
        path = _file_path(key)
        try:
            if path.is_file():
                path.unlink()
        except Exception as exc:
            log.warning("MoySklad ai_fill file delete failed: %s", exc)
        try:
            legacy_path = _legacy_store_path()
            if legacy_path.is_file():
                raw = json.loads(legacy_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and cid in raw:
                    raw.pop(cid, None)
                    legacy_path.write_text(
                        json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
        except Exception:
            pass
        return {"ok": True, "cleared": cid}

    # Clear all: memory + legacy file; Redis keys by pattern when available.
    with _LOCK:
        _MEMORY.clear()
    client = _redis_client()
    if client is not None:
        try:
            pattern = f"moysklad:ai-fill:v1:{_account_fingerprint()}:*"
            for key in client.scan_iter(match=pattern, count=200):
                client.delete(key)
        except Exception as exc:
            log.warning("MoySklad ai_fill Redis clear-all failed: %s", exc)
    cache_root = get_hermes_home() / "moysklad" / "ai_fill_cache"
    if cache_root.is_dir():
        for path in cache_root.glob("*.json"):
            try:
                path.unlink()
            except Exception:
                pass
    legacy_path = _legacy_store_path()
    try:
        if legacy_path.is_file():
            legacy_path.write_text("{}\n", encoding="utf-8")
    except Exception:
        pass
    return {"ok": True, "cleared": "all"}


def ai_group_labels_for_client(client_id: str) -> list[str]:
    """Return AI-stamped group labels for one client (empty if none)."""
    cid = str(client_id or "").strip()
    if not cid:
        return []
    entry = get_ai_fill_entry(cid)
    if not isinstance(entry, dict):
        return []
    fields = entry.get("fields") or {}
    value = fields.get("groups")
    # Prefer fields.groups even if ai_fields stamp was rewritten/lost.
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [p.strip() for p in re.split(r"[,;|/]", value) if p.strip()]
    return []
