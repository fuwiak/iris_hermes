"""Archived MoySklad sales channels must keep their historical names.

No Flowwow API — channel comes from MoySklad document + saleschannel entity.
"""

from __future__ import annotations

from plugins.moysklad.sales_channels import (
    NO_CHANNEL_LABEL,
    channel_category,
    channel_name_from_order,
    display_channel_label,
    format_channels_display,
    matches_marketplace_channel_name,
    resolve_channel_name,
    sales_channel_type_from_channels,
    sales_channels_by_id,
)


def _order_with_channel(*, name: str | None = None, channel_id: str | None = None) -> dict:
    if name is None and channel_id is None:
        return {}
    sc: dict = {}
    if name is not None:
        sc["name"] = name
    if channel_id is not None:
        sc["id"] = channel_id
        sc["meta"] = {
            "href": f"https://api.moysklad.ru/api/remap/1.2/entity/saleschannel/{channel_id}"
        }
    return {"salesChannel": sc}


def test_a_active_website_channel_displays_name() -> None:
    """A) active channel = Website → display Website."""
    order = _order_with_channel(name="Website")
    assert channel_name_from_order(order, {}) == "Website"
    assert display_channel_label("Website") == "Website"
    assert format_channels_display(["Website"]) == "Website"
    # Website is treated as direct (site-like); not forced marketplace.
    assert channel_category("Website") == "direct"


def test_b_archived_flowwow_skyloft_marketplace() -> None:
    """B) archived Flowwow Skyloft → name + marketplace category."""
    channels_by_id = {"sky-1": "Flowwow Skyloft"}
    order = _order_with_channel(channel_id="sky-1")  # no expand.name (typical archived)
    assert channel_name_from_order(order, channels_by_id) == "Flowwow Skyloft"
    assert matches_marketplace_channel_name("Flowwow Skyloft")
    assert channel_category("Flowwow Skyloft") == "marketplace"
    assert sales_channel_type_from_channels(["Flowwow Skyloft"]) == "маркетплейс"
    # Case / spacing robust
    assert matches_marketplace_channel_name("  flowwow   skyloft ")
    assert matches_marketplace_channel_name("FLOWWOW SKYLOFT")


def test_b_archived_resolved_via_get_by_id() -> None:
    """Archived channel missing from list → GET /entity/saleschannel/{id}."""
    channels_by_id: dict[str, str] = {}
    order = _order_with_channel(channel_id="archived-sky")

    def fetch(cid: str) -> dict:
        assert cid == "archived-sky"
        return {"id": cid, "name": "Flowwow Skyloft", "archived": True}

    name = resolve_channel_name(order, channels_by_id, fetch_channel=fetch)
    assert name == "Flowwow Skyloft"
    assert channels_by_id["archived-sky"] == "Flowwow Skyloft"
    assert channel_category(name) == "marketplace"


def test_c_missing_channel_is_bez_kanala() -> None:
    """C) channel absent → «Без канала»."""
    assert channel_name_from_order({}, {}) is None
    assert channel_name_from_order({"salesChannel": None}, {}) is None
    assert display_channel_label("") == NO_CHANNEL_LABEL
    assert display_channel_label(None) == NO_CHANNEL_LABEL
    assert format_channels_display([]) == NO_CHANNEL_LABEL
    assert format_channels_display(None) == "Без канала"


def test_d_unknown_archived_keeps_real_name() -> None:
    """D) unknown archived channel → real name; not forced marketplace."""
    channels_by_id = {"x-9": "Old Partner Desk"}
    order = _order_with_channel(channel_id="x-9")
    name = channel_name_from_order(order, channels_by_id)
    assert name == "Old Partner Desk"
    assert display_channel_label(name) == "Old Partner Desk"
    assert display_channel_label(name) != NO_CHANNEL_LABEL
    # Explicit marketplace mapping does not claim this name.
    assert not matches_marketplace_channel_name(name)
    assert channel_category(name) == "unknown"


def test_linked_but_unresolved_is_not_bez_kanala() -> None:
    """Document has salesChannel id but directory miss → not «Без канала» yet."""
    order = _order_with_channel(channel_id="missing-1")
    assert channel_name_from_order(order, {}) is None
    # After failed fetch, still no name — UI must not invent «Без канала»
    # for a *linked* id when we later surface a placeholder; format of empty
    # list is the only path to «Без канала».
    assert resolve_channel_name(order, {}, fetch_channel=lambda _cid: None) is None


def test_sales_channels_by_id_keeps_archived_rows() -> None:
    rows = [
        {"id": "a1", "name": "Website", "archived": False},
        {"id": "a2", "name": "Flowwow Skyloft", "archived": True},
    ]
    by_id = sales_channels_by_id(rows)
    assert by_id == {"a1": "Website", "a2": "Flowwow Skyloft"}
