"""Unit tests for MoySklad durable catalog cache (file backend, no Redis)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from plugins.moysklad import catalog_cache as cc
from plugins.moysklad.sales_channels import counterparty_row_from_api


@pytest.fixture
def hermes_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("MOYSKLAD_REDIS_URL", raising=False)
    monkeypatch.setenv("MOYSKLAD_API_TOKEN", "test-token-abc")
    monkeypatch.setenv("MOYSKLAD_CACHE_TTL_SECONDS", "3600")
    # Clear process memory between tests.
    cc._MEMORY.clear()
    return home


def test_file_cache_roundtrip(hermes_home: Path) -> None:
    key = cc.cache_key(max_orders=100, max_counterparties=0, include_archived=False)
    assert cc.get_cached(key) is None

    catalog = {"rows": [{"id": "1"}], "counts": {"total": 1}}
    synced = time.time()
    envelope = cc.set_cached(key, catalog, synced_at=synced)
    assert envelope["catalog"]["counts"]["total"] == 1

    hit = cc.get_cached(key)
    assert hit is not None
    assert hit["catalog"]["rows"][0]["id"] == "1"
    assert hit["synced_at"] == synced
    assert (hermes_home / "moysklad" / "cache").is_dir()
    assert cc.cache_backend_name() == "file"


def test_expired_cache_miss(hermes_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOYSKLAD_CACHE_TTL_SECONDS", "60")
    key = cc.cache_key(max_orders=50, max_counterparties=0, include_archived=False)
    cc.set_cached(key, {"rows": []}, synced_at=1.0)  # ancient
    assert cc.get_cached(key) is None


def test_invalidate_removes_file(hermes_home: Path) -> None:
    key = cc.cache_key(max_orders=10, max_counterparties=0, include_archived=False)
    cc.set_cached(key, {"rows": [1]})
    assert cc.get_cached(key) is not None
    cc.invalidate(key)
    assert cc.get_cached(key) is None


def test_counterparty_row_maps_crm_columns() -> None:
    row = counterparty_row_from_api(
        {
            "id": "cp-1",
            "name": "Alice",
            "phone": "+7999",
            "email": "a@example.com",
            "companyType": "individual",
            "actualAddress": "Москва",
            "sex": "FEMALE",
            "state": {"name": "новый"},
            "tags": ["витрина"],
            "attributes": [
                {"name": "Баллы начисленные", "value": 120},
                {"name": "Заказчик или получатель", "value": "Заказчик"},
                {"name": "Фактический адрес (Комментарий)", "value": "домофон 12"},
                {"name": "ТГ ник", "value": "@alice"},
                {"name": "TG conversation", "value": "https://t.me/c/1/2"},
            ],
        },
        order_channels=["Telegram"],
    )
    assert row["Наименование"] == "Alice"
    assert row["Телефон"] == "+7999"
    assert row["E-mail"] == "a@example.com"
    assert row["Тип контрагента"] == "Физическое лицо"
    assert row["Пол"] == "Женский"
    assert row["Фактический адрес"] == "Москва"
    assert row["Баллы начисленные"] == "120"
    assert row["Заказчик или получатель"] == "Заказчик"
    assert row["Фактический адрес (Комментарий)"] == "домофон 12"
    assert row["ТГ ник"] == "@alice"
    assert row["TG conversation"] == "https://t.me/c/1/2"
    assert row["Тип канала продаж"] == "прямые продажи"
