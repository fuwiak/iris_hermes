"""Partial rebuild flushes must never clobber the last full catalog.

Regression for the post-deploy incident (13.08.2026): a restart mid-rebuild
left the durable cache poisoned with ``partial=True`` — the UI saw 15 of 152
calendar matches until the next full rebuild. Partials now live on a
side-channel key; the main key always holds the last complete catalog.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from plugins.moysklad import catalog_cache as cc
from plugins.moysklad.dashboard import plugin_api as api


@pytest.fixture
def hermes_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("MOYSKLAD_REDIS_URL", raising=False)
    monkeypatch.setenv("MOYSKLAD_API_TOKEN", "test-token-abc")
    monkeypatch.setenv("MOYSKLAD_CACHE_TTL_SECONDS", "3600")
    cc._MEMORY.clear()
    cc._PAGE_MEMORY.clear()
    return home


def _full_catalog(rows: int) -> dict:
    return {
        "rows": [{"id": str(i), "Наименование": f"c{i}"} for i in range(rows)],
        "counts": {"total": rows},
        "partial": False,
    }


def test_partial_key_is_a_distinct_stable_key() -> None:
    key = cc.cache_key(max_orders=100, max_counterparties=0, include_archived=False)
    pkey = cc.partial_cache_key(key)
    assert pkey != key
    assert pkey == cc.partial_cache_key(key)
    assert pkey.startswith(key)


def test_partial_flush_does_not_clobber_full_cache(hermes_home: Path) -> None:
    key = cc.cache_key(max_orders=100, max_counterparties=0, include_archived=False)
    cc.set_cached(key, _full_catalog(152), synced_at=time.time())

    # Progressive rebuild flush (what _on_partial writes now).
    partial = {"rows": [{"id": "0"}], "partial": True}
    cc.set_cached(cc.partial_cache_key(key), partial, synced_at=time.time())

    env = cc.get_cached(key)
    assert env is not None
    assert not env["catalog"].get("partial")
    assert len(env["catalog"]["rows"]) == 152


def test_get_catalog_serves_full_while_partial_flushes_exist(
    hermes_home: Path,
) -> None:
    key = cc.cache_key(max_orders=100, max_counterparties=0, include_archived=False)
    cc.set_cached(key, _full_catalog(152), synced_at=time.time())
    cc.set_cached(
        cc.partial_cache_key(key),
        {"rows": [{"id": "0"}], "partial": True},
        synced_at=time.time(),
    )

    catalog, meta = api._get_catalog(
        max_orders=100, max_counterparties=0, include_archived=False, force=False
    )
    assert catalog is not None
    assert not catalog.get("partial")
    assert len(catalog.get("rows") or []) == 152
    assert meta.get("cached") is True


def test_get_catalog_falls_back_to_partial_when_no_full_exists(
    hermes_home: Path,
) -> None:
    key = cc.cache_key(max_orders=100, max_counterparties=0, include_archived=False)
    cc.set_cached(
        cc.partial_cache_key(key),
        {"rows": [{"id": "0"}, {"id": "1"}], "partial": True},
        synced_at=time.time(),
    )

    catalog, meta = api._get_catalog(
        max_orders=100,
        max_counterparties=0,
        include_archived=False,
        force=False,
        blocking=False,
    )
    assert catalog is not None
    assert catalog.get("partial") is True
    assert len(catalog.get("rows") or []) == 2
    assert meta.get("stale") is True


def test_keepwarm_tick_states(
    hermes_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = cc.cache_key(max_orders=25000, max_counterparties=0, include_archived=False)

    # Fresh full catalog → nothing to do.
    cc.set_cached(key, _full_catalog(3), synced_at=time.time())
    assert api.catalog_keepwarm_tick() == "fresh"

    # No token → keepwarm stays idle (no MoySklad calls possible).
    monkeypatch.setenv("MOYSKLAD_API_TOKEN", "")
    assert api.catalog_keepwarm_tick() == "no-token"


def test_keepwarm_interval_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MOYSKLAD_CATALOG_KEEPWARM_SECONDS", raising=False)
    assert api._keepwarm_interval_seconds() == 1800
    monkeypatch.setenv("MOYSKLAD_CATALOG_KEEPWARM_SECONDS", "0")
    assert api._keepwarm_interval_seconds() == 0
    monkeypatch.setenv("MOYSKLAD_CATALOG_KEEPWARM_SECONDS", "60")
    assert api._keepwarm_interval_seconds() == 300
    monkeypatch.setenv("MOYSKLAD_CATALOG_KEEPWARM_SECONDS", "7200")
    assert api._keepwarm_interval_seconds() == 7200
