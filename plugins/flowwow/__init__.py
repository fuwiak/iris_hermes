"""Flowwow marketplace integration plugin — opt-in standalone.

Same shape as ``plugins.moysklad``: sync HTTP client (Bearer token from
``FLOWWOW_API_TOKEN``), a handful of read tools, gated by
``check_flowwow_available`` so the tools stay registered but inert until a
token is configured.

Endpoints verified live against the official seller API 0.0.1
(``https://apis.flowwow.com/apiseller/...``) — see ``client.py`` docstring.
The open API has no orders/clients endpoints yet, so tools cover shops and
products (name, description, price, images) — the inputs for marketplace
card automation.
"""

from __future__ import annotations

from plugins.flowwow.tools import (
    FLOWWOW_HEALTH_SCHEMA,
    FLOWWOW_PRODUCTS_SCHEMA,
    FLOWWOW_SHOPS_SCHEMA,
    check_flowwow_available,
    handle_flowwow_health,
    handle_flowwow_products,
    handle_flowwow_shops,
)

_TOOLS = (
    ("flowwow_health", FLOWWOW_HEALTH_SCHEMA, handle_flowwow_health, "🌸"),
    ("flowwow_shops", FLOWWOW_SHOPS_SCHEMA, handle_flowwow_shops, "🏬"),
    ("flowwow_products", FLOWWOW_PRODUCTS_SCHEMA, handle_flowwow_products, "💐"),
)


def register(ctx) -> None:
    """Register all Flowwow tools. Called once by the plugin loader."""
    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="flowwow",
            schema=schema,
            handler=handler,
            check_fn=check_flowwow_available,
            emoji=emoji,
        )
