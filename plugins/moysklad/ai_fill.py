"""AI fill for empty MoySklad CRM fields (Группы, Статус, Пол, …).

Mirrors client_segmentation_deepseek: only fill blank cells, stamp
``_ai_fields`` so the UI can draw green AI markers. Overlays persist under
``$HERMES_HOME/moysklad/ai_fill.json`` and never overwrite MoySklad values.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from hermes_constants import get_hermes_home
from plugins.moysklad.assign_groups import heuristic_groups_for_row, merge_tags
from plugins.moysklad.sales_channels import moysklad_group_tokens

log = logging.getLogger(__name__)

_LOCK = threading.Lock()

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
- Статус: активный / спящий / новый / архивный — по заказам
- Пол: только если ясно из имени; иначе опусти поле
- ТГ ник: только если есть в данных; иначе опусти
- Не заполняй поля, которые уже не пустые в current
"""


def _store_path() -> Path:
    root = get_hermes_home() / "moysklad"
    root.mkdir(parents=True, exist_ok=True)
    return root / "ai_fill.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_store() -> dict[str, Any]:
    path = _store_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_store(data: dict[str, Any]) -> None:
    path = _store_path()
    tmp = path.with_suffix(".tmp")
    with _LOCK:
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)


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
    orders = int(row.get("order_count") or row.get("Всего заказов") or 0)
    last = str(row.get("last_order_at") or row.get("Дата последнего заказа") or "")
    if orders <= 0:
        return "новый"
    if orders == 1:
        return "новый"
    # crude: if last order year looks old
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


def heuristic_fill_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return public-key → value for empty fields only (heuristic)."""
    empty = set(empty_fillable_keys(row))
    out: dict[str, Any] = {}
    if "groups" in empty:
        existing = list(row.get("_moysklad_tags") or moysklad_group_tokens(row) or [])
        proposed = heuristic_groups_for_row(row)
        merged = merge_tags(existing, proposed)
        if merged and is_empty_cell(existing):
            out["groups"] = merged
        elif proposed and is_empty_cell(existing):
            out["groups"] = proposed
    if "state" in empty:
        out["state"] = _guess_state(row)
    if "sex" in empty:
        sex = _guess_sex_from_name(str(row.get("Наименование") or row.get("name") or ""))
        if sex:
            out["sex"] = sex
    if "role" in empty:
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
) -> dict[str, Any]:
    if not fills:
        return {"client_id": client_id, "ai_fields": [], "fields": {}, "source": source}
    store = _load_store()
    prev = store.get(client_id) if isinstance(store.get(client_id), dict) else {}
    fields = dict(prev.get("fields") or {})
    ai_fields = list(prev.get("ai_fields") or [])
    for key, value in fills.items():
        fields[key] = value
        if key not in ai_fields:
            ai_fields.append(key)
    entry = {
        "fields": fields,
        "ai_fields": ai_fields,
        "source": source,
        "updated_at": _now(),
    }
    store[client_id] = entry
    _save_store(store)
    return {"client_id": client_id, **entry}


def apply_ai_fill_to_public(client: dict[str, Any]) -> dict[str, Any]:
    """Merge persisted AI fills into a public client dict (empty slots only)."""
    cid = str(client.get("id") or "").strip()
    if not cid:
        client.setdefault("ai_fields", [])
        return client
    store = _load_store()
    entry = store.get(cid)
    if not isinstance(entry, dict):
        client.setdefault("ai_fields", [])
        return client
    fields = entry.get("fields") or {}
    ai_fields: list[str] = []
    out = dict(client)
    for key in _FILLABLE:
        if key not in fields:
            continue
        if not is_empty_cell(out.get(key)):
            continue
        value = fields[key]
        if key == "groups":
            if isinstance(value, list):
                out["groups"] = ", ".join(str(v) for v in value if str(v).strip())
                out["tags"] = [str(v).strip() for v in value if str(v).strip()]
            else:
                out["groups"] = str(value)
            ai_fields.append("groups")
        else:
            out[key] = value
            ai_fields.append(key)
    # Keep stamp even if MoySklad later filled — only show green on currently AI-shown
    out["ai_fields"] = ai_fields
    out["ai_fill_source"] = entry.get("source") or ""
    return out


def fill_empty_for_rows(
    rows: list[dict[str, Any]],
    *,
    client_ids: list[str] | None = None,
    limit: int = 40,
    use_llm: bool = True,
) -> dict[str, Any]:
    """Fill empty fields for matching rows. Returns summary + per-client results."""
    id_filter = {str(i).strip() for i in (client_ids or []) if str(i).strip()}
    targets: list[dict[str, Any]] = []
    for row in rows:
        cid = str(row.get("_moysklad_id") or row.get("id") or "").strip()
        if not cid:
            continue
        if id_filter and cid not in id_filter:
            continue
        if not empty_fillable_keys(row):
            continue
        targets.append(row)
        if len(targets) >= max(1, min(int(limit), 100)):
            break

    llm_map: dict[str, dict[str, Any]] = {}
    source = "heuristic"
    if use_llm and targets:
        llm_map = _llm_fill_batch(targets)
        if llm_map:
            source = "llm"

    results = []
    filled_fields = 0
    for row in targets:
        cid = str(row.get("_moysklad_id") or row.get("id") or "").strip()
        empty = set(empty_fillable_keys(row))
        heur = heuristic_fill_row(row)
        llm = {k: v for k, v in (llm_map.get(cid) or {}).items() if k in empty}
        # LLM wins when present; heuristic fills the rest
        merged = dict(heur)
        merged.update(llm)
        merged = {k: v for k, v in merged.items() if k in empty and not is_empty_cell(v)}
        row_source = "llm" if any(k in llm for k in merged) else "heuristic"
        if llm and heur and any(k not in llm for k in merged):
            row_source = "llm+heuristic"
        entry = _persist_fills(cid, merged, source=row_source)
        filled_fields += len(merged)
        results.append({
            "id": cid,
            "name": row.get("Наименование") or row.get("name") or "",
            "filled": merged,
            "ai_fields": entry.get("ai_fields") or [],
            "source": row_source,
            "empty_before": sorted(empty),
        })

    return {
        "ok": True,
        "source": source,
        "scanned": len(targets),
        "updated": len(results),
        "filled_field_count": filled_fields,
        "results": results,
    }


def clear_ai_fill(client_id: str = "") -> dict[str, Any]:
    store = _load_store()
    if client_id:
        store.pop(client_id, None)
        _save_store(store)
        return {"ok": True, "cleared": client_id}
    _save_store({})
    return {"ok": True, "cleared": "all"}
