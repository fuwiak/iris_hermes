"""«Карточки» marketplace feed: sections, slimming, cache."""

from __future__ import annotations

import plugins.moysklad.marketplace_cards as mc


def _reset_cache(monkeypatch=None):
    mc._cache["ts"] = 0.0
    mc._cache["payload"] = None
    mc._cache["key"] = ""
    if monkeypatch is not None:
        # Keep unit tests off the real Elasticsearch/Redis/file layer.
        monkeypatch.setattr(mc, "_durable_get", lambda key: None)
        monkeypatch.setattr(mc, "_durable_set", lambda key, payload: None)


def test_unconfigured_marketplaces(monkeypatch):
    _reset_cache(monkeypatch)
    monkeypatch.delenv("FLOWWOW_API_TOKEN", raising=False)
    monkeypatch.delenv("YANDEX_MARKET_API_TOKEN", raising=False)
    monkeypatch.delenv("YANDEX_MARKET_TOKEN", raising=False)
    payload = mc.marketplace_cards_payload(force=True)
    assert payload["flowwow"]["configured"] is False
    assert "FLOWWOW_API_TOKEN" in payload["flowwow"]["note"]
    assert payload["yandex"]["configured"] is False
    assert "YANDEX_MARKET_API_TOKEN" in payload["yandex"]["note"]


def test_slim_product_trims_and_normalizes():
    slim = mc._slim_product(
        {
            "productId": 7,
            "name": "Букет",
            "description": "x" * 500,
            "price": "5990.00",
            "discount": "10",
            "currencyCode": "RUB",
            "isActive": True,
            "isArchived": False,
            "url": "https://flowwow.com/x",
            "images": ["a.jpg", "b.jpg"],
        }
    )
    assert slim["product_id"] == 7
    assert len(slim["description_preview"]) == 200
    assert slim["image"] == "a.jpg"
    assert slim["images_count"] == 2
    assert slim["is_active"] is True


def test_flowwow_section_maps_shop_and_products(monkeypatch):
    _reset_cache(monkeypatch)
    monkeypatch.setenv("FLOWWOW_API_TOKEN", "tok")
    monkeypatch.delenv("YANDEX_MARKET_API_TOKEN", raising=False)
    monkeypatch.delenv("YANDEX_MARKET_TOKEN", raising=False)

    import plugins.flowwow.client as fw

    class _FakeClient:
        def shops(self, *, status="active"):
            assert status == "active"
            return {"rows": [{"shopId": 32992, "name": "Вереск", "address": "Москва"}], "total": 1}

        def products(self, shop_id, *, limit=0, with_archive=False, extended=False):
            assert shop_id == 32992
            return {"rows": [{"productId": 1, "name": "Роза", "images": []}], "total": 1}

    monkeypatch.setattr(fw, "FlowwowClient", _FakeClient)
    payload = mc.marketplace_cards_payload(limit=10, force=True)
    fwsec = payload["flowwow"]
    assert fwsec["configured"] is True
    assert fwsec["shop"] == {"shop_id": 32992, "name": "Вереск", "address": "Москва"}
    assert fwsec["products"][0]["name"] == "Роза"
    assert fwsec["total"] == 1


def test_payload_cached_between_calls(monkeypatch):
    _reset_cache(monkeypatch)
    monkeypatch.delenv("FLOWWOW_API_TOKEN", raising=False)
    first = mc.marketplace_cards_payload(force=True)
    monkeypatch.setenv("FLOWWOW_API_TOKEN", "tok")  # would change the section…
    second = mc.marketplace_cards_payload()  # …but cache serves the old payload
    assert second is first


def test_durable_cache_survives_process_restart(monkeypatch):
    _reset_cache(monkeypatch)
    stored: dict = {}
    monkeypatch.setattr(mc, "_durable_get", lambda key: stored.get(key))
    monkeypatch.setattr(mc, "_durable_set", lambda key, payload: stored.update({key: payload}))
    monkeypatch.delenv("FLOWWOW_API_TOKEN", raising=False)
    monkeypatch.delenv("YANDEX_MARKET_API_TOKEN", raising=False)
    monkeypatch.delenv("YANDEX_MARKET_TOKEN", raising=False)

    first = mc.marketplace_cards_payload(limit=7, force=True)
    assert stored[mc._durable_key(7)] is first  # written to ES/Redis/file layer

    # Simulate restart: in-process cache gone, durable layer still there.
    mc._cache["ts"] = 0.0
    mc._cache["payload"] = None
    mc._cache["key"] = ""
    calls = {"n": 0}
    real_section = mc._flowwow_section
    monkeypatch.setattr(
        mc, "_flowwow_section", lambda limit: calls.__setitem__("n", calls["n"] + 1) or real_section(limit)
    )
    second = mc.marketplace_cards_payload(limit=7)
    assert second is first
    assert calls["n"] == 0  # nothing refetched


def test_force_bypasses_durable_cache(monkeypatch):
    _reset_cache(monkeypatch)
    monkeypatch.setattr(mc, "_durable_get", lambda key: {"flowwow": {"stale": True}})
    monkeypatch.delenv("FLOWWOW_API_TOKEN", raising=False)
    payload = mc.marketplace_cards_payload(force=True)
    assert "stale" not in payload["flowwow"]


def test_combined_cards_merge_across_marketplaces():
    flowwow = {
        "products": [
            {"name": "❣️ Букет «Розовый сад»", "image": "f.jpg", "is_active": True},
            {"name": "Пионы Корал Шарм", "is_active": False},
        ]
    }
    yandex = {
        "products": [
            {"name": "Букет Розовый сад", "image": "y.jpg", "is_active": True, "content_rating": 91},
            {"name": "Гладиолусы", "is_archived": True},
        ]
    }
    rows = mc.build_combined_cards(flowwow, yandex)
    merged = next(r for r in rows if "Розовый сад" in r["name"])
    assert sorted(merged["marketplaces"]) == ["flowwow", "yandex_market"]
    assert merged["image"] == "f.jpg"  # first seen wins
    assert merged["listings"]["yandex_market"]["content_rating"] == 91
    only_fw = next(r for r in rows if "Пионы" in r["name"])
    assert only_fw["marketplaces"] == ["flowwow"]
    assert only_fw["statuses"] == ["hidden"]
    archived = next(r for r in rows if "Гладиолусы" in r["name"])
    assert archived["statuses"] == ["archived"]
