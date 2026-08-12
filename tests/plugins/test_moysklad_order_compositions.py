"""Order composition (MoySklad positions) enrichment for AI / client card."""

from __future__ import annotations

from plugins.moysklad.client_card import build_client_detail, heuristic_ai
from plugins.moysklad.order_compositions import (
    apply_composition_to_order,
    enrich_orders_with_compositions,
    looks_like_order_code,
    position_lines_from_payload,
)
from plugins.moysklad.outreach import _historical_bouquet_candidates, facts_panel


def test_position_lines_from_assortment():
    payload = {
        "rows": [
            {"assortment": {"name": "Пионы белые"}, "quantity": 5},
            {"assortment": {"name": "Эвкалипт"}, "quantity": 1.5},
            {"name": "Лента"},
        ]
    }
    lines = position_lines_from_payload(payload)
    assert lines == ["Пионы белые ×5", "Эвкалипт ×1.5", "Лента"]


def test_looks_like_order_code():
    assert looks_like_order_code("1605-02")
    assert looks_like_order_code("#12345")
    assert not looks_like_order_code("Букет пионов")


def test_apply_composition_replaces_code_snippet():
    order = {"product_snippet": "1605-02", "name": "1605-02", "description": ""}
    apply_composition_to_order(order, ["Розы ×11", "Эвкалипт ×1"])
    assert order["composition"] == "Розы ×11; Эвкалипт ×1"
    assert order["line_items"] == ["Розы ×11", "Эвкалипт ×1"]
    assert "Розы" in order["product_snippet"]
    assert "1605-02" not in order["product_snippet"]


def test_enrich_orders_fetches_missing_positions():
    orders = [
        {"id": "o-new", "moment": "2026-08-01 10:00:00", "product_snippet": "1605-02"},
        {
            "id": "o-old",
            "moment": "2025-01-01 10:00:00",
            "line_items": ["Уже есть ×1"],
            "composition": "Уже есть ×1",
        },
    ]
    calls: list[str] = []

    def fetch(oid: str):
        calls.append(oid)
        return {"rows": [{"assortment": {"name": "Гортензия"}, "quantity": 3}]}

    filled = enrich_orders_with_compositions(orders, fetch_positions=fetch, max_orders=8)
    assert filled == 1
    assert calls == ["o-new"]
    assert orders[0]["composition"] == "Гортензия ×3"
    assert orders[1]["composition"] == "Уже есть ×1"


def test_build_client_detail_enriches_via_fetch_positions():
    row = {
        "_moysklad_id": "cp-1",
        "Наименование": "Мария",
        "_orders_context": [
            {
                "id": "ord-1",
                "name": "1605-02",
                "moment": "2026-05-16 12:00:00",
                "sum": 550000,
                "product_snippet": "1605-02",
            }
        ],
    }

    def fetch(_oid: str):
        return {
            "rows": [
                {"assortment": {"name": "Пионовидные розы"}, "quantity": 15},
                {"assortment": {"name": "Эвкалипт"}, "quantity": 2},
            ]
        }

    detail = build_client_detail(
        row,
        fetch_positions=fetch,
        enrich_compositions=True,
    )
    order = detail["orders"][0]
    assert order["composition"]
    assert "Пионовидные розы" in order["composition"]
    assert order["line_items"]
    panel = facts_panel(detail)
    assert panel["orders_preview"][0]["composition"]
    blocks = detail.get("fact_blocks") or {}
    history = (blocks.get("history_profile") or {}).get("lines") or []
    history_blob = " ".join(str(x.get("value") or "") for x in history if isinstance(x, dict))
    assert "Пионовидные" in history_blob or "Пионовидные" in (order["composition"] or "")
    cands = _historical_bouquet_candidates(detail)
    assert cands and "Пионовидные" in cands[0]["product"]
    ai = heuristic_ai(
        detail["client"],
        detail["orders"],
        vip=False,
        loyalty=None,
        data_thin=False,
        risks=detail.get("risks"),
    )
    assert "Пионовидные" in ai["recommendation"]
