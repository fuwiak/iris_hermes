"""Flowwow marketplace integration plugin — opt-in standalone.

Same shape as ``plugins.moysklad``: sync HTTP client (Bearer token from
``FLOWWOW_API_TOKEN``), a handful of read tools, gated by
``check_flowwow_available`` so the tools stay registered but inert until a
token is configured.

Endpoint paths in ``client.py`` follow the common Flowwow seller-API
conventions but are not verified against live Flowwow docs from this
environment — run ``flowwow_health`` after setting the token; if it 404s,
adjust ``FLOWWOW_API_URL`` / the paths in ``client.py`` to match what your
seller cabinet actually exposes.
"""

from __future__ import annotations

from plugins.flowwow.tools import (
    FLOWWOW_CLIENTS_SCHEMA,
    FLOWWOW_HEALTH_SCHEMA,
    FLOWWOW_ORDERS_SCHEMA,
    check_flowwow_available,
    handle_flowwow_clients,
    handle_flowwow_health,
    handle_flowwow_orders,
)

_TOOLS = (
    ("flowwow_health", FLOWWOW_HEALTH_SCHEMA, handle_flowwow_health, "🌸"),
    ("flowwow_orders", FLOWWOW_ORDERS_SCHEMA, handle_flowwow_orders, "📦"),
    ("flowwow_clients", FLOWWOW_CLIENTS_SCHEMA, handle_flowwow_clients, "🧑‍🤝‍🧑"),
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
