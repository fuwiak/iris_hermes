"""MoySklad tool schemas + handlers (registered via plugins/moysklad)."""

from __future__ import annotations

from typing import Any

from plugins.moysklad.classify import clients_by_sales_type
from plugins.moysklad.client import MoySkladClient, MoySkladError, token_configured
from tools.registry import tool_error, tool_result


def check_moysklad_available() -> bool:
    return token_configured()


def _int_arg(raw: Any, default: int, *, minimum: int = 0, maximum: int = 5000) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _bool_arg(raw: Any, default: bool = False) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        cleaned = raw.strip().lower()
        if cleaned in {"1", "true", "yes", "on"}:
            return True
        if cleaned in {"0", "false", "no", "off"}:
            return False
    return default


def _tags_arg(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    return [t.strip() for t in str(raw).split(",") if t.strip()]


def _handle(fn):
    try:
        return tool_result(fn(MoySkladClient()))
    except MoySkladError as exc:
        return tool_error(str(exc), status_code=exc.status_code)
    except Exception as exc:  # pragma: no cover — defensive
        return tool_error(f"MoySklad failed: {type(exc).__name__}: {exc}")


MOYSKLAD_HEALTH_SCHEMA = {
    "name": "moysklad_health",
    "description": (
        "Check MoySklad API token and connectivity (Remap 1.2). "
        "Returns ok + counterparty total when healthy."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

MOYSKLAD_COUNTERPARTIES_SCHEMA = {
    "name": "moysklad_counterparties",
    "description": (
        "List MoySklad counterparties (clients). Optional name search. "
        "Prefer small limit; use fetch_all only when needed."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Page size (default 50). With fetch_all, max rows (0=no cap).",
            },
            "offset": {"type": "integer", "description": "Pagination offset (default 0)."},
            "search": {
                "type": "string",
                "description": "Partial name filter (MoySklad name~).",
            },
            "fetch_all": {
                "type": "boolean",
                "description": "Paginate through all matching rows (slow on large accounts).",
            },
            "include_archived": {
                "type": "boolean",
                "description": "Include archived counterparties (default false).",
            },
        },
        "required": [],
    },
}

MOYSKLAD_ORDERS_SCHEMA = {
    "name": "moysklad_orders",
    "description": (
        "List MoySklad customer orders. Optionally filter by counterparty UUID "
        "(agent_id). Expands agent, state, salesChannel."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Page size (default 50)."},
            "offset": {"type": "integer", "description": "Pagination offset."},
            "agent_id": {
                "type": "string",
                "description": "Counterparty UUID to filter orders.",
            },
            "fetch_all": {
                "type": "boolean",
                "description": "Paginate all matching orders.",
            },
        },
        "required": [],
    },
}

MOYSKLAD_POSITIONS_SCHEMA = {
    "name": "moysklad_positions",
    "description": "Fetch line items (positions) for one MoySklad customer order.",
    "parameters": {
        "type": "object",
        "properties": {
            "order_id": {
                "type": "string",
                "description": "Customer order UUID.",
            },
        },
        "required": ["order_id"],
    },
}

MOYSKLAD_CHANNELS_SCHEMA = {
    "name": "moysklad_channels",
    "description": "List MoySklad sales channels.",
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Page size (default 100)."},
            "offset": {"type": "integer"},
            "fetch_all": {"type": "boolean"},
        },
        "required": [],
    },
}

MOYSKLAD_PUSH_TAGS_SCHEMA = {
    "name": "moysklad_push_tags",
    "description": (
        "Replace tags on a MoySklad counterparty (PUT). Overwrites the full tag "
        "list — confirm with the user before calling."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "counterparty_id": {
                "type": "string",
                "description": "Counterparty UUID.",
            },
            "tags": {
                "description": "Tag list (array of strings, or comma-separated string).",
            },
        },
        "required": ["counterparty_id", "tags"],
    },
}

MOYSKLAD_CLIENTS_BY_SALES_TYPE_SCHEMA = {
    "name": "moysklad_clients_by_sales_type",
    "description": (
        "List MoySklad clients filtered like Iris CRM tabs «Маркетплейс» / "
        "«Прямые». Uses the same channel allowlists (FlowWow vs Telegram/"
        "WhatsApp/Витрина/сайт). Prefer this over inventing classification."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "sales_filter": {
                "type": "string",
                "description": (
                    "direct | marketplace | all "
                    "(also: прямые, маркетплейс). Default all."
                ),
            },
            "limit": {
                "type": "integer",
                "description": "Max clients to return (default 50, max 500).",
            },
            "max_orders": {
                "type": "integer",
                "description": "Max orders to scan for channels (default 5000, 0=all).",
            },
            "max_counterparties": {
                "type": "integer",
                "description": "Max counterparties to scan (default 0=all).",
            },
            "include_archived": {
                "type": "boolean",
                "description": "Include archived counterparties (default false).",
            },
        },
        "required": [],
    },
}


def handle_moysklad_health(args: dict, **kw) -> str:
    return _handle(lambda c: c.health())


def handle_moysklad_counterparties(args: dict, **kw) -> str:
    return _handle(
        lambda c: c.counterparties(
            limit=_int_arg(args.get("limit"), 50),
            offset=_int_arg(args.get("offset"), 0),
            search=str(args.get("search") or "").strip(),
            fetch_all=_bool_arg(args.get("fetch_all"), False),
            include_archived=_bool_arg(args.get("include_archived"), False),
        )
    )


def handle_moysklad_orders(args: dict, **kw) -> str:
    return _handle(
        lambda c: c.orders(
            limit=_int_arg(args.get("limit"), 50),
            offset=_int_arg(args.get("offset"), 0),
            agent_id=str(args.get("agent_id") or "").strip(),
            fetch_all=_bool_arg(args.get("fetch_all"), False),
        )
    )


def handle_moysklad_positions(args: dict, **kw) -> str:
    order_id = str(args.get("order_id") or "").strip()
    if not order_id:
        return tool_error("order_id is required")
    return _handle(lambda c: c.positions(order_id))


def handle_moysklad_channels(args: dict, **kw) -> str:
    return _handle(
        lambda c: c.channels(
            limit=_int_arg(args.get("limit"), 100),
            offset=_int_arg(args.get("offset"), 0),
            fetch_all=_bool_arg(args.get("fetch_all"), False),
        )
    )


def handle_moysklad_push_tags(args: dict, **kw) -> str:
    counterparty_id = str(args.get("counterparty_id") or "").strip()
    tags = _tags_arg(args.get("tags"))
    if not counterparty_id:
        return tool_error("counterparty_id is required")
    if not tags:
        return tool_error("tags must be a non-empty list")
    return _handle(lambda c: c.push_tags(counterparty_id, tags))


def handle_moysklad_clients_by_sales_type(args: dict, **kw) -> str:
    sales_filter = str(args.get("sales_filter") or "all").strip() or "all"
    return _handle(
        lambda c: clients_by_sales_type(
            c,
            sales_filter=sales_filter,
            limit=_int_arg(args.get("limit"), 50, maximum=500),
            max_orders=_int_arg(args.get("max_orders"), 5000, maximum=100_000),
            max_counterparties=_int_arg(
                args.get("max_counterparties"), 0, maximum=100_000
            ),
            include_archived=_bool_arg(args.get("include_archived"), False),
        )
    )
