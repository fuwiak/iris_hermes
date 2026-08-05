"""Tests for MoySklad local TG conversation store."""

from __future__ import annotations

from plugins.moysklad.client_card import build_client_detail
from plugins.moysklad.conversations import (
    append_message,
    enrich_client_row,
    get_thread,
    preview_text,
    seed_from_moysklad_attr,
)
from plugins.moysklad.outreach import facts_panel


def test_append_outbound_and_preview(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    thread = append_message(
        client_id="cp-1",
        text="Здравствуйте! Сверимся по оплате.",
        direction="outbound",
        channel="telegram",
        phone="+7 (900) 111-22-33",
        tg_nick="@maria",
        client_name="Мария",
        source="campaign_send",
    )
    assert thread["message_count"] == 1
    assert thread["messages"][0]["direction"] == "outbound"
    assert "исходящее" in thread["messages"][0]["label"].lower()
    assert "telegram" in thread["messages"][0]["label"].lower()
    prev = preview_text(thread)
    assert "оплат" in prev.lower()

    # Lookup by phone / nick
    by_phone = get_thread(phone="79001112233")
    assert by_phone["message_count"] == 1
    by_nick = get_thread(tg_nick="maria")
    assert by_nick["message_count"] == 1


def test_inbound_append_same_thread(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    append_message(
        client_id="cp-2",
        text="Исходящее",
        direction="outbound",
        channel="whatsapp",
    )
    thread = append_message(
        client_id="cp-2",
        text="Ок, переведу завтра",
        direction="inbound",
        channel="whatsapp",
    )
    assert thread["message_count"] == 2
    assert thread["messages"][-1]["direction"] == "inbound"
    assert "входящее" in thread["messages"][-1]["label"].lower()


def test_seed_from_attr_once(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    first = seed_from_moysklad_attr(
        client_id="cp-3",
        attr_value="Клиент писал: нужна доставка к пятнице",
        phone="79001110000",
    )
    assert first["message_count"] == 1
    assert first["messages"][0]["source"] == "moysklad_attr"
    second = seed_from_moysklad_attr(
        client_id="cp-3",
        attr_value="другой текст не должен дублировать",
        phone="79001110000",
    )
    assert second["message_count"] == 1


def test_url_attr_not_seeded_as_message(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    thread = seed_from_moysklad_attr(
        client_id="cp-4",
        attr_value="https://t.me/c/1/2",
    )
    assert thread["empty"] is True


def test_enrich_and_facts_include_conversation(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    append_message(
        client_id="cp-out-1",
        text="Черновик для фактов",
        direction="outbound",
        channel="telegram",
        phone="+7 (900) 555-11-22",
    )
    row = {
        "_moysklad_id": "cp-out-1",
        "Наименование": "Мария Букет",
        "Телефон": "+7 (900) 555-11-22",
        "_moysklad_tags": [],
        "order_count": 1,
        "avg_check": 1000.0,
        "_orders_context": [
            {
                "id": "o1",
                "moment": "2025-03-01 11:00:00",
                "sum": 1000,
                "channel": "Telegram",
                "product_snippet": "Розы",
            }
        ],
        "_audience": {"direct": True, "marketplace": False},
    }
    detail = build_client_detail(row)
    assert detail["conversation"]["message_count"] >= 1
    panel = facts_panel(detail)
    assert panel["conversation"]["message_count"] >= 1
    assert panel["conversation"]["messages"]
    public = enrich_client_row(
        {
            "id": "cp-out-1",
            "name": "Мария",
            "phone": "+7 (900) 555-11-22",
            "tg_conversation": "",
        }
    )
    assert public["tg_conversation_preview"]
    assert "Черновик" in public["tg_conversation"]
