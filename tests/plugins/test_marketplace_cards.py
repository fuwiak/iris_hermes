"""«Карточки» marketplace feed: sections, slimming, cache."""

from __future__ import annotations

import plugins.moysklad.marketplace_cards as mc


def _reset_cache():
    mc._cache["ts"] = 0.0
    mc._cache["payload"] = None


def test_unconfigured_marketplaces(monkeypatch):
    _reset_cache()
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
    _reset_cache()
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
    _reset_cache()
    monkeypatch.delenv("FLOWWOW_API_TOKEN", raising=False)
    first = mc.marketplace_cards_payload(force=True)
    monkeypatch.setenv("FLOWWOW_API_TOKEN", "tok")  # would change the section…
    second = mc.marketplace_cards_payload()  # …but cache serves the old payload
    assert second is first
