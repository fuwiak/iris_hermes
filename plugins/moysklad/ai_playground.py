"""AI client-card playground over the golden eval fixture (~20 clients).

Lets a human inspect each generation stage: raw input → LLM facts JSON →
fact blocks → heuristic / LLM (Саммари, Повод/intent, Рекомендация).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from plugins.moysklad.client_card import (
    _AI_SYSTEM,
    _facts_payload,
    build_client_detail,
    build_fact_blocks,
    compute_risks,
    generate_ai_for_detail,
    heuristic_ai,
)

log = logging.getLogger(__name__)

_GOLDEN_PATH = Path(__file__).resolve().parent / "eval" / "golden_clients_v1.json"
_fixture_cache: Optional[dict[str, Any]] = None


def ai_system_prompt() -> str:
    return _AI_SYSTEM


def golden_path() -> Path:
    return _GOLDEN_PATH


def load_golden_fixture(*, force: bool = False) -> dict[str, Any]:
    """Load golden_clients_v1.json (cached in-process)."""
    global _fixture_cache
    if _fixture_cache is not None and not force:
        return _fixture_cache
    path = _GOLDEN_PATH
    if not path.is_file():
        raise FileNotFoundError(f"golden fixture missing: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("golden fixture must be a JSON object")
    _fixture_cache = data
    return data


def list_golden_clients() -> dict[str, Any]:
    data = load_golden_fixture()
    clients = []
    for row in data.get("clients") or []:
        if not isinstance(row, dict):
            continue
        channels = [str(c) for c in (row.get("channels") or []) if str(c).strip()]
        clients.append(
            {
                "id": str(row.get("id") or "").strip(),
                "name": str(row.get("name") or "").strip() or "—",
                "order_count": int(row.get("order_count") or len(row.get("orders") or [])),
                "avg_check": float(row.get("avg_check") or 0),
                "channels": channels,
                "phone": str(row.get("phone") or "").strip() or None,
                "tags_count": len(row.get("tags") or []),
            }
        )
    selection = data.get("selection") if isinstance(data.get("selection"), dict) else {}
    return {
        "ok": True,
        "version": data.get("version"),
        "generated_at": data.get("generated_at"),
        "count": len(clients),
        "selection": selection,
        "clients": clients,
    }


def get_golden_client(client_id: str) -> dict[str, Any]:
    cid = (client_id or "").strip()
    if not cid:
        raise KeyError("client_id required")
    data = load_golden_fixture()
    for row in data.get("clients") or []:
        if isinstance(row, dict) and str(row.get("id") or "").strip() == cid:
            return row
    raise KeyError(f"golden client not found: {cid}")


def golden_to_catalog_row(client: dict[str, Any]) -> dict[str, Any]:
    """Map golden-fixture client → catalog row accepted by build_client_detail."""
    channels = [str(c) for c in (client.get("channels") or []) if str(c).strip()]
    orders_raw = []
    for o in client.get("orders") or []:
        if not isinstance(o, dict):
            continue
        line_items = o.get("line_items") or []
        snippet = str(o.get("product_snippet") or "").strip()
        if not snippet and line_items:
            snippet = "; ".join(str(x) for x in line_items[:6])
        orders_raw.append(
            {
                "id": o.get("id"),
                "name": o.get("name") or "",
                "moment": o.get("date") or o.get("moment") or "",
                "sum": o.get("sum"),
                "payed_sum": o.get("payed_sum"),
                "unpaid": o.get("unpaid"),
                "channel": o.get("channel") or "",
                "description": o.get("description") or "",
                "product_snippet": snippet,
                "state": o.get("state"),
            }
        )
    last_at = ""
    if orders_raw:
        dates = [str(o.get("moment") or "") for o in orders_raw if o.get("moment")]
        last_at = max(dates) if dates else ""

    return {
        "_moysklad_id": str(client.get("id") or "").strip(),
        "Наименование": str(client.get("name") or "").strip(),
        "Телефон": str(client.get("phone") or "").strip(),
        "email": str(client.get("email") or "").strip(),
        "E-mail": str(client.get("email") or "").strip(),
        "_moysklad_tags": list(client.get("tags") or []),
        "_moysklad_state": str(client.get("state") or "").strip(),
        "Статус": str(client.get("state") or "").strip(),
        "Тип контрагента": str(client.get("company_type") or "").strip(),
        "Пол": str(client.get("sex") or "").strip(),
        "Заказчик или получатель": str(client.get("role") or "").strip(),
        "Фактический адрес": str(client.get("actual_address") or "").strip(),
        "ТГ ник": str(client.get("tg_nick") or "").strip(),
        "TG conversation": str(client.get("tg_conversation") or "").strip(),
        "Баллы начисленные": client.get("bonus_points") or "",
        "order_count": int(client.get("order_count") or len(orders_raw)),
        "avg_check": float(client.get("avg_check") or 0),
        "last_order_at": last_at,
        "Канал продаж": channels[0] if channels else "",
        "Тип канала продаж": "",
        "balance": client.get("balance"),
        "_orders_context": orders_raw,
        "_audience": {"direct": True, "marketplace": False},
    }


def detail_from_facts_payload(facts: dict[str, Any]) -> dict[str, Any]:
    """Rebuild a client-card detail from editable LLM-input JSON."""
    if not isinstance(facts, dict):
        raise ValueError("facts must be a JSON object")
    client = dict(facts.get("client") or {})
    orders = [o for o in (facts.get("orders") or []) if isinstance(o, dict)]
    data_thin = bool(facts.get("data_thin"))
    risks_in = facts.get("risks")
    risks = (
        risks_in
        if isinstance(risks_in, dict)
        else compute_risks(client, orders, data_thin=data_thin)
    )
    vip = bool(client.get("vip"))
    loyalty = client.get("loyalty_points")
    detail: dict[str, Any] = {
        "ok": True,
        "client": client,
        "orders": orders,
        "stats": {
            "avg_check": float(client.get("avg_check") or 0),
            "order_count": int(client.get("order_count") or len(orders)),
            "vip": vip,
            "loyalty_points": loyalty,
            "last_order": orders[0] if orders else None,
            "balance": client.get("balance"),
            "has_debt": bool(risks.get("has_debt")),
            "do_not_upsell": bool(risks.get("do_not_upsell")),
        },
        "messaging": {},
        "data_thin": data_thin,
        "risks": risks,
        "conversation": facts.get("conversation")
        if isinstance(facts.get("conversation"), dict)
        else {"messages": [], "message_count": 0, "preview": "", "empty": True},
        "ai": heuristic_ai(
            client,
            orders,
            vip=vip,
            loyalty=loyalty if isinstance(loyalty, (int, float)) else None,
            data_thin=data_thin,
            risks=risks,
        ),
    }
    detail["fact_blocks"] = build_fact_blocks(detail)
    return detail


def _pretty(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def build_playground_trace(
    detail: dict[str, Any],
    *,
    run_llm: bool = False,
    source_label: str = "detail",
    raw_input: Any = None,
) -> dict[str, Any]:
    """Assemble stage-by-stage view for the playground UI."""
    client = detail.get("client") or {}
    llm_input = _facts_payload(detail)
    heuristic = detail.get("ai") if isinstance(detail.get("ai"), dict) else None
    if not heuristic or heuristic.get("source") not in (None, "heuristic"):
        # Prefer a fresh deterministic baseline for the playground.
        heuristic = heuristic_ai(
            client,
            list(detail.get("orders") or []),
            vip=bool(client.get("vip")),
            loyalty=client.get("loyalty_points")
            if isinstance(client.get("loyalty_points"), (int, float))
            else None,
            data_thin=bool(detail.get("data_thin")),
            risks=detail.get("risks"),
        )
    else:
        heuristic = {
            "history_profile": heuristic.get("history_profile") or "",
            "occasion_intent": heuristic.get("occasion_intent") or "",
            "recommendation": heuristic.get("recommendation") or "",
            "source": "heuristic",
            "data_thin": bool(detail.get("data_thin")),
        }

    fact_blocks = detail.get("fact_blocks") or build_fact_blocks(detail)
    llm_block: Optional[dict[str, Any]] = None
    if run_llm:
        llm_block = generate_ai_for_detail(detail)

    active = llm_block if (llm_block and llm_block.get("source") == "llm") else heuristic

    stages = {
        "input_raw": raw_input if raw_input is not None else client,
        "llm_input": llm_input,
        "system_prompt": ai_system_prompt(),
        "fact_blocks": fact_blocks,
        "heuristic": heuristic,
        "llm": llm_block,
        "active": {
            "history_profile": (active or {}).get("history_profile") or "",
            "occasion_intent": (active or {}).get("occasion_intent") or "",
            "recommendation": (active or {}).get("recommendation") or "",
            "source": (active or {}).get("source") or "heuristic",
        },
    }

    return {
        "ok": True,
        "source": source_label,
        "client_id": str(client.get("id") or ""),
        "client_name": str(client.get("name") or ""),
        "data_thin": bool(detail.get("data_thin")),
        "stages": stages,
        "panels": {
            "input_text": _pretty(llm_input),
            "outputs": {
                "history_profile": stages["active"]["history_profile"],
                "occasion_intent": stages["active"]["occasion_intent"],
                "recommendation": stages["active"]["recommendation"],
                "fact_blocks": _pretty(fact_blocks),
                "heuristic": _pretty(heuristic),
                "llm": _pretty(llm_block) if llm_block else "",
                "system_prompt": ai_system_prompt(),
                "full": _pretty(stages),
            },
        },
    }


def run_playground(
    *,
    client_id: str = "",
    input_json: str = "",
    run_llm: bool = False,
) -> dict[str, Any]:
    """Resolve golden client and/or edited facts JSON → playground trace."""
    raw_input: Any = None
    detail: Optional[dict[str, Any]] = None
    source = "golden"

    edited = (input_json or "").strip()
    if edited:
        try:
            parsed = json.loads(edited)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid input_json: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("input_json must be a JSON object")
        raw_input = parsed
        # Accept either full golden client or LLM facts payload.
        if "orders" in parsed and "client" not in parsed and "name" in parsed:
            detail = build_client_detail(golden_to_catalog_row(parsed))
            source = "golden_edited"
        elif "client" in parsed or "orders" in parsed:
            detail = detail_from_facts_payload(parsed)
            source = "facts_edited"
        else:
            raise ValueError("input_json needs client+orders or a golden client object")
    elif client_id.strip():
        golden = get_golden_client(client_id.strip())
        raw_input = golden
        detail = build_client_detail(golden_to_catalog_row(golden))
        source = "golden"
    else:
        raise ValueError("provide client_id or input_json")

    assert detail is not None
    return build_playground_trace(
        detail,
        run_llm=run_llm,
        source_label=source,
        raw_input=raw_input,
    )
