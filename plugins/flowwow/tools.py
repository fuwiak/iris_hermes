"""Flowwow tool schemas + handlers (registered via plugins/flowwow)."""

from __future__ import annotations

from typing import Any

from plugins.flowwow.client import FlowwowClient, FlowwowError, token_configured
from tools.registry import tool_error, tool_result


def check_flowwow_available() -> bool:
    return token_configured()


def _int_arg(raw: Any, default: int, *, minimum: int = 0, maximum: int = 5000) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _handle(fn):
    try:
        return tool_result(fn(FlowwowClient()))
    except FlowwowError as exc:
        return tool_error(str(exc), status_code=exc.status_code)
    except Exception as exc:  # pragma: no cover — defensive
        return tool_error(f"Flowwow failed: {type(exc).__name__}: {exc}")


FLOWWOW_HEALTH_SCHEMA = {
    "name": "flowwow_health",
    "description": (
        "Check the Flowwow API token and base URL by fetching one order. "
        "Returns ok + a sample order id when healthy."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}


def handle_flowwow_health(_args: dict[str, Any]) -> str:
    return _handle(lambda client: client.health())


FLOWWOW_ORDERS_SCHEMA = {
    "name": "flowwow_orders",
    "description": "List Flowwow orders (optionally filtered by status).",
    "parameters": {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "description": "Optional Flowwow order status filter (e.g. new, paid, cancelled).",
            },
            "limit": {
                "type": "integer",
                "description": "Max rows to fetch (0 = all pages, capped at 5000).",
                "default": 0,
            },
        },
        "required": [],
    },
}


def handle_flowwow_orders(args: dict[str, Any]) -> str:
    status = str(args.get("status") or "").strip() or None
    limit = _int_arg(args.get("limit"), 0)
    return _handle(lambda client: client.orders(status=status, limit=limit))


FLOWWOW_CLIENTS_SCHEMA = {
    "name": "flowwow_clients",
    "description": "List Flowwow buyers/clients known to this shop.",
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Max rows to fetch (0 = all pages, capped at 5000).",
                "default": 0,
            },
        },
        "required": [],
    },
}


def handle_flowwow_clients(args: dict[str, Any]) -> str:
    limit = _int_arg(args.get("limit"), 0)
    return _handle(lambda client: client.clients(limit=limit))
