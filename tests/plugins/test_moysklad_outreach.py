"""Tests for MoySklad outreach draft grounding + campaign client sync fields."""

from __future__ import annotations

import plugins.moysklad.campaigns as campaigns
from plugins.moysklad.client_card import build_client_detail
from plugins.moysklad.outreach import (
    facts_panel,
    heuristic_outreach_message,
    _parse_outreach_json,
)


def _sample_row(**overrides):
    base = {
        "_moysklad_id": "cp-out-1",
        "Наименование": "Мария Букет",
        "Телефон": "+7 (900) 555-11-22",
        "E-mail": "maria@example.com",
        "_moysklad_tags": ["#постоянный", "событие марта"],
        "_moysklad_state": "активный",
        "Тип канала продаж": "Прямые",
        "Канал продаж": "WhatsApp",
        "Баллы начисленные": "40",
        "ТГ ник": "",
        "order_count": 2,
        "avg_check": 3800.0,
        "last_order_at": "2025-03-01 11:00:00",
        "_audience": {"direct": True, "marketplace": False},
        "_orders_context": [
            {
                "id": "o1",
                "name": "100",
                "moment": "2025-03-01 11:00:00",
                "sum": 4000,
                "channel": "WhatsApp",
                "product_snippet": "Пионы",
            },
            {
                "id": "o2",
                "name": "101",
                "moment": "2024-02-10 09:00:00",
                "sum": 3600,
                "channel": "Telegram",
                "product_snippet": "Розы",
            },
        ],
    }
    base.update(overrides)
    return base


def test_facts_panel_exposes_audit_fields():
    detail = build_client_detail(_sample_row())
    panel = facts_panel(detail)
    assert panel["name"] == "Мария Букет"
    assert panel["order_count"] == 2
    assert panel["avg_check"] == 3800.0
    assert panel["phone"]
    assert len(panel["orders_preview"]) >= 1
    assert panel["recommendation"]


def test_heuristic_outreach_cites_facts_not_discounts():
    detail = build_client_detail(_sample_row())
    out = heuristic_outreach_message(detail, channel="whatsapp")
    msg = out["message"].lower()
    assert "iris" in msg or "здравств" in msg
    assert "2025-03-01" in out["message"] or "пион" in msg
    assert "скидк" not in msg
    assert "промокод" not in msg
    assert "-50%" not in out["message"]
    assert out["source"] == "heuristic"
    assert out["facts"]["client_id"]


def test_heuristic_outreach_thin_data_avoids_fake_history():
    row = _sample_row()
    row["_orders_context"] = []
    row["order_count"] = 0
    row["avg_check"] = 0
    row["Телефон"] = ""
    detail = build_client_detail(row)
    out = heuristic_outreach_message(detail, channel="telegram")
    low = out["message"].lower()
    assert "скидк" not in low
    assert "vip" not in low or "не" in low
    assert out["facts"]["data_thin"] is True


def test_parse_outreach_json():
    parsed = _parse_outreach_json(
        '```json\n{"message":"Привет","grounding_notes":"даты заказов"}\n```'
    )
    assert parsed == {"message": "Привет", "grounding_notes": "даты заказов"}


def test_create_draft_stores_client_facts(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    detail = build_client_detail(_sample_row())
    panel = facts_panel(detail)
    item = campaigns.create_draft(
        title="Черновик · Мария",
        channel="whatsapp",
        mode="auto",
        offer="Здравствуйте, Мария!",
        sales_filter="direct",
        audience_count=1,
        client_id="cp-out-1",
        client_name="Мария Букет",
        facts=panel,
        recommendation=panel.get("recommendation") or "",
        grounding_notes="только факты",
        ai_source="heuristic",
    )
    listed = campaigns.list_campaigns()
    assert listed[0]["client_id"] == "cp-out-1"
    assert listed[0]["facts"]["order_count"] == 2
    assert listed[0]["ai_source"] == "heuristic"
    assert item["offer"].startswith("Здравствуйте")
