"""moysklad plugin — MoySklad Remap 1.2 tools + Clients dashboard.

Opt-in standalone plugin (same enable/disable pattern as google_meet):

    hermes plugins enable moysklad
    hermes plugins disable moysklad

Dashboard tab «Клиенты» (`/clients`) appears when enabled.
Then toggle the ``moysklad`` toolset per surface in ``hermes tools``.

Requires ``MOYSKLAD_API_TOKEN`` in ``~/.hermes/.env``.
"""

from __future__ import annotations

from plugins.moysklad.tools import (
    MOYSKLAD_CHANNELS_SCHEMA,
    MOYSKLAD_CLIENTS_BY_SALES_TYPE_SCHEMA,
    MOYSKLAD_COUNTERPARTIES_SCHEMA,
    MOYSKLAD_HEALTH_SCHEMA,
    MOYSKLAD_ORDERS_SCHEMA,
    MOYSKLAD_POSITIONS_SCHEMA,
    MOYSKLAD_PUSH_TAGS_SCHEMA,
    check_moysklad_available,
    handle_moysklad_channels,
    handle_moysklad_clients_by_sales_type,
    handle_moysklad_counterparties,
    handle_moysklad_health,
    handle_moysklad_orders,
    handle_moysklad_positions,
    handle_moysklad_push_tags,
)

_TOOLS = (
    ("moysklad_health", MOYSKLAD_HEALTH_SCHEMA, handle_moysklad_health, "🩺"),
    ("moysklad_counterparties", MOYSKLAD_COUNTERPARTIES_SCHEMA, handle_moysklad_counterparties, "👥"),
    ("moysklad_orders", MOYSKLAD_ORDERS_SCHEMA, handle_moysklad_orders, "🧾"),
    ("moysklad_positions", MOYSKLAD_POSITIONS_SCHEMA, handle_moysklad_positions, "📦"),
    ("moysklad_channels", MOYSKLAD_CHANNELS_SCHEMA, handle_moysklad_channels, "📡"),
    ("moysklad_push_tags", MOYSKLAD_PUSH_TAGS_SCHEMA, handle_moysklad_push_tags, "🏷️"),
    (
        "moysklad_clients_by_sales_type",
        MOYSKLAD_CLIENTS_BY_SALES_TYPE_SCHEMA,
        handle_moysklad_clients_by_sales_type,
        "🗂️",
    ),
)


def register(ctx) -> None:
    """Register MoySklad tools when plugin is in ``plugins.enabled``."""
    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="moysklad",
            schema=schema,
            handler=handler,
            check_fn=check_moysklad_available,
            requires_env=["MOYSKLAD_API_TOKEN"],
            emoji=emoji,
        )
    # Fast campaign drafts: same flash model as Iris chat, but no mid-reasoning.
    # ``auxiliary.compression`` stays on medium for client-card summaries.
    ctx.register_auxiliary_task(
        key="moysklad_outreach",
        display_name="MoySklad outreach",
        description="Campaign draft / rewrite / bouquet text (low latency)",
        defaults={
            "provider": "auto",
            "model": "deepseek/deepseek-v4-flash-0731",
            "timeout": 45,
            "reasoning_effort": "none",
            "extra_body": {"reasoning": {"enabled": False}},
        },
    )
