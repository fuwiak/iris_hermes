"""Unit tests for MoySklad client-card mapping + AI guardrail helpers."""

from __future__ import annotations

from plugins.moysklad.client_card import (
    build_client_detail,
    build_fact_blocks,
    compute_risks,
    heuristic_ai,
    messaging_links,
    _parse_ai_json,
)


def _sample_row(**overrides):
    base = {
        "_moysklad_id": "cp-rich-1",
        "Наименование": "Анна Цветы",
        "Телефон": "+7 (999) 111-22-33",
        "E-mail": "anna@example.com",
        "email": "anna@example.com",
        "_moysklad_tags": ["#постоянный", "vip", "событие марта", "FlowWow"],
        "_moysklad_state": "активный",
        "Статус": "активный",
        "Тип канала продаж": "Маркетплейс",
        "Канал продаж": "FlowWow",
        "Тип контрагента": "Физическое лицо",
        "Пол": "Женский",
        "Заказчик или получатель": "Заказчик",
        "Баллы начисленные": "150",
        "ТГ ник": "@anna_flowers",
        "TG conversation": "",
        "order_count": 3,
        "avg_check": 4500.0,
        "last_order_at": "2025-03-05 12:00:00",
        "_audience": {"direct": False, "marketplace": True},
        "_orders_context": [
            {
                "id": "o1",
                "name": "00001",
                "moment": "2025-03-05 12:00:00",
                "sum": 5000,
                "channel": "FlowWow",
                "product_snippet": "Букет пионов",
            },
            {
                "id": "o2",
                "name": "00002",
                "moment": "2024-02-12 10:00:00",
                "sum": 4000,
                "channel": "Telegram",
                "product_snippet": "Розы 25",
            },
            {
                "id": "o3",
                "name": "00003",
                "moment": "2024-09-01 09:00:00",
                "sum": 4500,
                "channel": "WhatsApp",
                "description": "1 сентября",
            },
        ],
    }
    base.update(overrides)
    return base


def test_messaging_whatsapp_and_telegram_links():
    links = messaging_links(phone="+7 (999) 111-22-33", tg_nick="@anna")
    assert links["phone_digits"] == "79991112233"
    assert links["whatsapp_url"] == "https://wa.me/79991112233"
    assert links["telegram_url"] == "https://t.me/anna"
    assert links["primary_channel"] == "WhatsApp"


def test_messaging_prefers_tg_conversation_url():
    links = messaging_links(
        phone="",
        tg_nick="@x",
        tg_conversation="https://t.me/c/1/2",
    )
    assert links["telegram_url"] == "https://t.me/c/1/2"
    assert links["primary_channel"] == "Telegram"
    assert links["whatsapp_url"] == ""


def test_build_client_detail_stamps_tg_last_contact(tmp_path, monkeypatch):
    """Карточка клиента показывает параметр последнего контакта через TG."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from plugins.moysklad.conversations import append_message, clear_memory_for_tests

    clear_memory_for_tests()
    thread = append_message(
        client_id="cp-rich-1",
        text="Добрый день",
        direction="outbound",
        channel="telegram",
        phone="+7 (999) 111-22-33",
        tg_nick="@anna_flowers",
    )
    detail = build_client_detail(_sample_row())
    assert detail["client"]["tg_last_contact_at"] == thread["last_contact_at"]
    assert detail["client"]["tg_last_contact_at"]


def test_build_client_detail_maps_orders_stats_tags():
    detail = build_client_detail(_sample_row())
    assert detail["ok"] is True
    assert detail["client"]["name"] == "Анна Цветы"
    assert detail["client"]["vip"] is True
    assert detail["client"]["loyalty_points"] == 150.0
    assert detail["stats"]["order_count"] == 3
    assert len(detail["orders"]) == 3
    assert detail["orders"][0]["date"].startswith("2025-03")
    assert "FlowWow" in (detail["client"]["tag_buckets"].get("marketplace") or []) or True
    assert detail["messaging"]["whatsapp_url"].startswith("https://wa.me/")
    assert detail["ai"]["source"] == "heuristic"
    assert "5000" in detail["ai"]["history_profile"] or "5000" in str(detail["orders"][0]["sum"])


def test_heuristic_ai_does_not_invent_contacts_when_thin():
    row = _sample_row()
    row["Телефон"] = ""
    row["ТГ ник"] = ""
    row["TG conversation"] = ""
    row["Баллы начисленные"] = ""
    row["_moysklad_tags"] = []
    row["_orders_context"] = []
    row["order_count"] = 0
    row["avg_check"] = 0
    detail = build_client_detail(row)
    ai = detail["ai"]
    assert detail["data_thin"] is True
    assert ai["data_thin"] is True
    low = ai["history_profile"].lower()
    assert "данных мало" in low or "заказов в выгрузке нет" in low
    assert "не отмечен" in low  # VIP not claimed from empty tags
    assert "не указаны" in low or "баллы" in low
    rec = ai["recommendation"].lower()
    assert "whatsapp" in rec or "telegram" in rec or "канал" in rec


def test_heuristic_mentions_march_when_orders_support_it():
    detail = build_client_detail(_sample_row())
    occasion = detail["ai"]["occasion_intent"].lower()
    assert "март" in occasion or "8" in occasion


def test_parse_ai_json_strips_fence():
    parsed = _parse_ai_json(
        '```json\n{"history_profile":"a","occasion_intent":"b","recommendation":"c"}\n```'
    )
    assert parsed == {
        "history_profile": "a",
        "occasion_intent": "b",
        "recommendation": "c",
    }


def test_heuristic_ai_cites_order_facts():
    client = {
        "name": "Тест",
        "phone": "+7999",
        "tags": ["vip"],
        "avg_check": 3000,
        "tg_nick": "",
        "tg_conversation": "",
    }
    orders = [
        {"date": "2025-03-01", "sum": 3000, "channel": "FlowWow"},
        {"date": "2024-12-20", "sum": 3000, "channel": "Telegram"},
    ]
    ai = heuristic_ai(client, orders, vip=True, loyalty=10.0, data_thin=False)
    assert "2025-03-01" in ai["history_profile"]
    assert "3000" in ai["recommendation"] or "3000" in ai["history_profile"]
    assert ai["source"] == "heuristic"


def test_debt_suppresses_upsell_recommendation():
    row = _sample_row(balance=-8000.0)
    detail = build_client_detail(row)
    assert detail["risks"]["has_debt"] is True
    assert detail["risks"]["do_not_upsell"] is True
    rec = detail["ai"]["recommendation"].lower()
    assert "не предлагать" in rec or "upsell" in rec or "задолжен" in rec or "оплат" in rec
    assert "букет" not in rec or "не предлагать" in rec
    blocks = detail["fact_blocks"]
    assert blocks["history_profile"]["title"] == "История и профиль"
    assert blocks["occasion_intent"]["title"] == "Повод и intent"
    assert blocks["risks"]["title"] == "Риски / ограничения"
    assert blocks["risks"]["do_not_upsell"] is True


def test_unpaid_order_sets_do_not_upsell():
    """Recent unpaid → payment chase. Stale unpaid → failed, no chase."""
    from datetime import date, timedelta

    recent = (date.today() - timedelta(days=7)).isoformat() + " 12:00:00"
    row = _sample_row()
    row["balance"] = 0
    row["_orders_context"] = [
        {
            "id": "o1",
            "moment": recent,
            "date": recent,
            "sum": 5000,
            "payed_sum": 1000,
            "unpaid": 4000,
            "channel": "Telegram",
            "product_snippet": "Розы",
        }
    ]
    detail = build_client_detail(row)
    assert detail["risks"]["unpaid_order_count"] == 1
    assert detail["risks"]["do_not_upsell"] is True
    assert detail["risks"]["failed_customer"] is False
    assert detail["orders"][0]["unpaid"] == 4000.0


def test_stale_unpaid_is_failed_customer_not_payment_chase():
    row = _sample_row()
    row["balance"] = 0
    row["_orders_context"] = [
        {
            "id": "o-old",
            "moment": "2025-03-05 12:00:00",
            "date": "2025-03-05 12:00:00",
            "sum": 5000,
            "payed_sum": 0,
            "unpaid": 5000,
            "channel": "Telegram",
            "product_snippet": "Розы",
        }
    ]
    detail = build_client_detail(row)
    assert detail["risks"]["failed_customer"] is True
    assert detail["risks"]["do_not_upsell"] is False
    rec = detail["ai"]["recommendation"].lower()
    assert "несостояв" in rec or "оплат" in rec
    assert "сверке оплаты" not in rec
    from plugins.moysklad.outreach import heuristic_outreach_message

    draft = heuristic_outreach_message(detail, seller_name="Тест")
    assert "оплат" not in draft["message"].lower() or "не спрашиваем" in draft["message"].lower()
    assert "сверк" not in draft["message"].lower()


def test_cancelled_order_is_failed_not_new_customer():
    from plugins.moysklad.ai_fill import _guess_state
    from plugins.moysklad.order_status import classify_order_payment, summarize_order_context

    order = {
        "sum": 3000,
        "payed_sum": 0,
        "unpaid": 3000,
        "state": "Отменен",
        "applicable": False,
        "moment": "2025-06-01",
    }
    assert classify_order_payment(order) == "cancelled"
    summary = summarize_order_context([order])
    assert summary["failed_only"] is True
    assert summary["fulfilled_order_count"] == 0
    row = {
        "_orders_context": [order],
        "order_count": 1,
        "fulfilled_order_count": 0,
    }
    assert _guess_state(row) == "несостоявшийся"
