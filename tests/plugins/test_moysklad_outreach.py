"""Tests for MoySklad outreach draft grounding + campaign client sync fields."""

from __future__ import annotations

import plugins.moysklad.campaigns as campaigns
from plugins.moysklad.client_card import build_client_detail
from plugins.moysklad.outreach import (
    _OUTREACH_SYSTEM,
    facts_panel,
    heuristic_outreach_message,
    rewrite_outreach_message,
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
    out = heuristic_outreach_message(
        detail,
        channel="whatsapp",
        seller_name="Анна из Iris",
        seller_facts="Доставка по городу, акцент на сезонные букеты",
    )
    msg = out["message"]
    low = msg.lower()
    assert "анна" in low or "iris" in low
    assert "2025-03-01" not in msg
    assert "пион" in low
    assert "скидк" not in low
    assert "промокод" not in low
    assert "навязан" not in low
    assert "(whatsapp)" not in low
    assert "-50%" not in msg
    assert out["source"] == "heuristic"
    assert out["facts"]["client_id"]
    assert out["seller_name"] == "Анна из Iris"


def test_heuristic_skips_internal_order_codes():
    row = _sample_row()
    row["_orders_context"] = [
        {
            "id": "o1",
            "name": "1605-02",
            "moment": "2026-05-16 12:00:00",
            "sum": 10790,
            "channel": "Telegram",
            "product_snippet": "1605-02",
        }
    ]
    row["avg_check"] = 10790.0
    row["last_order_at"] = "2026-05-16 12:00:00"
    detail = build_client_detail(row)
    out = heuristic_outreach_message(detail, channel="telegram", seller_name="Анна")
    msg = out["message"]
    assert "1605-02" not in msg
    assert "2026-05-16" not in msg
    assert "мая" in msg.lower() or "помог" in msg.lower()
    assert "(whatsapp)" not in msg.lower()
    assert "(telegram)" not in msg.lower()


def test_heuristic_outreach_thin_data_avoids_fake_history():
    row = _sample_row()
    row["_orders_context"] = []
    row["order_count"] = 0
    row["avg_check"] = 0
    row["Телефон"] = ""
    detail = build_client_detail(row)
    out = heuristic_outreach_message(
        detail, channel="telegram", seller_name="Магазин Роза"
    )
    low = out["message"].lower()
    assert "скидк" not in low
    assert "vip" not in low or "не" in low
    assert "роза" in low or "магазин" in low
    assert out["facts"]["data_thin"] is True


def test_outreach_system_prompt_includes_seller_fields():
    prompt = _OUTREACH_SYSTEM("Анна из Iris", "Адрес: ул. Цветочная 1")
    assert "Анна из Iris" in prompt
    assert "ул. Цветочная 1" in prompt
    assert "Это Iris" in prompt  # instruction: don't hardcode unless signature says so
    assert "навязанных скидок" in prompt


def test_rewrite_heuristic_removes_robot_meta():
    draft = (
        "Здравствуйте, Анатолий! Это Iris. Последний заказ у нас был 2026-05-16 "
        "(1605-02). Ориентир по прошлым заказам ≈ 10790 ₽. Напишите, если удобно "
        "продолжить подбор — без навязанных скидок, только по вашей истории. (WhatsApp)"
    )
    out = rewrite_outreach_message(
        draft,
        channel="telegram",
        seller_name="Анна из Iris",
    )
    # Without LLM this path is heuristic_rewrite (or llm if available)
    msg = out["message"]
    low = msg.lower()
    assert "навязан" not in low
    assert "только по вашей истории" not in low
    assert "1605-02" not in msg
    assert "(whatsapp)" not in low
    assert out["source"] in ("heuristic_rewrite", "llm_rewrite")


def test_parse_outreach_json():
    parsed = _parse_outreach_json(
        '```json\n{"message":"Привет","grounding_notes":"даты заказов"}\n```'
    )
    assert parsed == {"message": "Привет", "grounding_notes": "даты заказов"}


def test_seller_settings_persist(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    saved = campaigns.save_seller_settings(
        seller_name="Анна из Iris",
        seller_facts="Сезонные букеты, доставка",
    )
    assert saved["seller_name"] == "Анна из Iris"
    loaded = campaigns.get_seller_settings()
    assert loaded["seller_facts"] == "Сезонные букеты, доставка"


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
