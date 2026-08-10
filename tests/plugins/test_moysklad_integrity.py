"""Проверка таблицы Клиенты — data-quality audit."""

from __future__ import annotations

from datetime import date

from plugins.moysklad.integrity import audit_catalog


def _catalog(rows: list[dict]) -> dict:
    return {"rows": rows}


def _issue(report: dict, code: str) -> dict | None:
    for issue in report["issues"]:
        if issue["code"] == code:
            return issue
    return None


def test_clean_catalog_reports_nothing():
    report = audit_catalog(
        _catalog([
            {
                "_moysklad_id": "a",
                "Наименование": "Мария",
                "Телефон": "+7 900 555-11-22",
                "_moysklad_tags": ["8 марта"],
                "order_count": 2,
                "paid_order_count": 2,
                "avg_check": 4200,
                "_orders_context": [{"Канал продаж": "Telegram"}],
            }
        ])
    )
    assert report["clean"] is True
    assert report["issues"] == []
    assert report["rows_total"] == 1


def test_unreachable_and_nameless_rows_are_errors():
    report = audit_catalog(
        _catalog([
            {"_moysklad_id": "a", "Наименование": "", "Телефон": "", "ТГ ник": ""},
        ])
    )
    assert _issue(report, "no_name")["severity"] == "error"
    assert _issue(report, "unreachable")["count"] == 1
    assert report["errors_total"] >= 2


def test_duplicate_phone_across_cards():
    report = audit_catalog(
        _catalog([
            {"_moysklad_id": "a", "Наименование": "Мария", "Телефон": "+7 900 555-11-22"},
            {"_moysklad_id": "b", "Наименование": "Мария Б.", "Телефон": "8 (900) 555-11-22"},
            {"_moysklad_id": "c", "Наименование": "Олег", "Телефон": "+7 901 000-00-00"},
        ])
    )
    dup = _issue(report, "dup_phone")
    assert dup is not None
    assert dup["count"] == 2
    assert {s["id"] for s in dup["sample"]} == {"a", "b"}


def test_money_and_date_contradictions():
    report = audit_catalog(
        _catalog([
            {
                "_moysklad_id": "a",
                "Наименование": "Без заказов, но с чеком",
                "Телефон": "+7 900 555-11-22",
                "order_count": 0,
                "avg_check": 3000,
                "_moysklad_tags": ["x"],
            },
            {
                "_moysklad_id": "b",
                "Наименование": "Заказ из будущего",
                "Телефон": "+7 900 555-11-23",
                "order_count": 1,
                "paid_order_count": 1,
                "avg_check": 100,
                "last_order_at": "2099-01-01",
                "_orders_context": [{"Канал продаж": "Telegram"}],
                "_moysklad_tags": ["x"],
            },
            {
                "_moysklad_id": "c",
                "Наименование": "ДР в будущем",
                "Телефон": "+7 900 555-11-24",
                "Дата рождения": "2099-05-05",
                "_moysklad_tags": ["x"],
            },
        ]),
        today=date(2026, 8, 10),
    )
    assert _issue(report, "money_without_orders")["count"] == 1
    assert _issue(report, "future_order")["count"] == 1
    assert _issue(report, "bad_birthdate")["count"] == 1


def test_orders_without_channel_and_debt():
    report = audit_catalog(
        _catalog([
            {
                "_moysklad_id": "a",
                "Наименование": "Заказ без канала",
                "Телефон": "+7 900 555-11-22",
                "order_count": 3,
                "paid_order_count": 3,
                "avg_check": 1000,
                "balance": -4500.0,
                "_moysklad_tags": ["x"],
            }
        ])
    )
    assert _issue(report, "orders_without_channel")["count"] == 1
    assert _issue(report, "debt")["count"] == 1
    # Errors sort above warnings, warnings above info.
    severities = [i["severity"] for i in report["issues"]]
    assert severities == sorted(
        severities, key=lambda s: {"error": 0, "warn": 1, "info": 2}[s]
    )
