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
        "Check the Flowwow seller API: ping + one shops page. "
        "Returns ok, active shop count and a sample shop when the token works."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}


def handle_flowwow_health(_args: dict[str, Any]) -> str:
    return _handle(lambda client: client.health())


FLOWWOW_SHOPS_SCHEMA = {
    "name": "flowwow_shops",
    "description": (
        "List Flowwow shops for this seller account (shopId, name, address, "
        "status). shopId is needed for flowwow_products."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["moderation", "active", "disabled"],
                "description": "Shop status filter (default active).",
                "default": "active",
            },
        },
        "required": [],
    },
}


def handle_flowwow_shops(args: dict[str, Any]) -> str:
    status = str(args.get("status") or "active").strip() or "active"
    return _handle(lambda client: client.shops(status=status))


FLOWWOW_PRODUCTS_SCHEMA = {
    "name": "flowwow_products",
    "description": (
        "List products of one Flowwow shop: name, description, price, discount, "
        "images, availability. Get shopId from flowwow_shops first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "shop_id": {
                "type": "integer",
                "description": "Flowwow shopId (from flowwow_shops).",
            },
            "limit": {
                "type": "integer",
                "description": "Max rows to fetch (0 = all pages, capped at 5000).",
                "default": 0,
            },
            "with_archive": {
                "type": "boolean",
                "description": "Include archived products.",
                "default": False,
            },
            "extended": {
                "type": "boolean",
                "description": "Include the extended `properties` object per product.",
                "default": False,
            },
        },
        "required": ["shop_id"],
    },
}


def handle_flowwow_products(args: dict[str, Any]) -> str:
    shop_id = _int_arg(args.get("shop_id"), 0, minimum=0, maximum=4294967295)
    if not shop_id:
        return tool_error("shop_id is required — call flowwow_shops to find it.")
    limit = _int_arg(args.get("limit"), 0)
    with_archive = bool(args.get("with_archive"))
    extended = bool(args.get("extended"))
    return _handle(
        lambda client: client.products(
            shop_id,
            limit=limit,
            with_archive=with_archive,
            extended=extended,
        )
    )
