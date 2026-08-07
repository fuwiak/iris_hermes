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
    cc._PAGE_MEMORY.clear()
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


def test_peek_cached_returns_stale(hermes_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Expired envelope must remain peekable for stale-while-revalidate."""
    monkeypatch.setenv("MOYSKLAD_CACHE_TTL_SECONDS", "60")
    key = cc.cache_key(max_orders=50, max_counterparties=0, include_archived=False)
    cc.set_cached(key, {"rows": [{"id": "stale-1"}], "counts": {"total": 1}}, synced_at=1.0)
    assert cc.get_cached(key) is None
    peeked = cc.peek_cached(key)
    assert peeked is not None
    assert peeked["catalog"]["rows"][0]["id"] == "stale-1"


def test_redis_retention_exceeds_logical_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOYSKLAD_CACHE_TTL_SECONDS", "3600")
    monkeypatch.delenv("MOYSKLAD_CACHE_REDIS_RETENTION_SECONDS", raising=False)
    assert cc.redis_retention_seconds() >= cc.cache_ttl_seconds() * 7


def test_page_snapshot_roundtrip_first_100(hermes_home: Path) -> None:
    key = cc.page_snapshot_key(sales_filter="direct", group="", q="", group_source="any")
    clients = [{"id": str(i), "name": f"c{i}"} for i in range(120)]
    page = {
        "clients": clients,
        "counts": {"total": 120, "direct": 120, "marketplace": 0},
        "matched_total": 120,
        "has_more": True,
        "next_offset": 100,
    }
    env = cc.set_page_snapshot(key, page, synced_at=123.0)
    assert len(env["page"]["clients"]) == cc.PAGE_SNAPSHOT_ROWS
    hit = cc.get_page_snapshot(key)
    assert hit is not None
    sliced = cc.slice_page_snapshot(hit, limit=100, offset=0)
    assert sliced is not None
    assert len(sliced["clients"]) == 100
    assert sliced["has_more"] is True
    assert cc.slice_page_snapshot(hit, limit=50, offset=5) is None  # only offset 0


def test_invalidate_removes_file(hermes_home: Path) -> None:
    key = cc.cache_key(max_orders=10, max_counterparties=0, include_archived=False)
    cc.set_cached(key, {"rows": [1]})
    assert cc.get_cached(key) is not None
    cc.invalidate(key)
    assert cc.get_cached(key) is None


def test_refresh_audience_counts_fixes_stale_cache_totals(hermes_home: Path) -> None:
    """Stale catalog counts (pre-partition) must be rewritten on refresh."""
    rows = [
        {
            "_moysklad_id": "d1",
            "_orders_context": [{"Канал продаж": "Telegram"}],
            "_moysklad_tags": [],
        },
        {
            "_moysklad_id": "m1",
            "_orders_context": [{"Канал продаж": "FlowWow Floday"}],
            "_moysklad_tags": [],
        },
        {
            "_moysklad_id": "d2",
            "_orders_context": [],
            "_moysklad_tags": [],
        },
    ]
    stale = {
        "rows": rows,
        # Old broken numbers: gap + undercounted direct
        "counts": {"direct": 1, "marketplace": 1, "other": 1, "total": 3},
    }
    fresh = cc.refresh_audience_counts(stale)
    assert fresh["total"] == 3
    assert fresh["other"] == 0
    assert fresh["direct"] + fresh["marketplace"] == fresh["total"]
    assert stale["counts"] == fresh


def test_clients_page_ignores_stale_catalog_counts() -> None:
    from plugins.moysklad.classify import clients_page

    catalog = {
        "rows": [
            {
                "_moysklad_id": "1",
                "Наименование": "A",
                "_orders_context": [{"Канал продаж": "Telegram"}],
                "_moysklad_tags": [],
                "_audience": {"direct": False, "marketplace": False},
            },
            {
                "_moysklad_id": "2",
                "Наименование": "B",
                "_orders_context": [{"Канал продаж": "Ozon"}],
                "_moysklad_tags": [],
            },
        ],
        "counts": {"direct": 203, "marketplace": 6018, "other": 3358, "total": 2},
        "orders_scanned": 0,
        "counterparties_scanned": 2,
    }

    class _Dummy:
        pass

    page = clients_page(_Dummy(), sales_filter="all", catalog=catalog, limit=50)
    counts = page["counts"]
    assert counts["total"] == 2
    assert counts["other"] == 0
    assert counts["direct"] + counts["marketplace"] == counts["total"]
    assert counts["direct"] == 1
    assert counts["marketplace"] == 1


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
