"""Yandex Market client: auth, paging, card slimming."""

from __future__ import annotations

import json

import plugins.moysklad.yandex_market as ym


def _resp(status: int, body: dict):
    class _Resp:
        status_code = status
        content = json.dumps(body).encode()

        def json(self):
            return json.loads(self.content)

    r = _Resp()
    r.text = json.dumps(body)
    return r


def _fake_client(handler):
    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def request(self, method, url, headers=None, params=None, json=None):
            return handler(method, url, headers, params, json)

    return _Client


def test_missing_token_raises(monkeypatch):
    monkeypatch.delenv("YANDEX_MARKET_API_TOKEN", raising=False)
    monkeypatch.delenv("YANDEX_MARKET_TOKEN", raising=False)
    try:
        ym.YandexMarketClient()
        assert False, "expected YandexMarketError"
    except ym.YandexMarketError as exc:
        assert "YANDEX_MARKET_API_TOKEN" in str(exc)


def test_strip_html():
    assert ym.strip_html("<p>Розы <b>в горшках</b></p>") == "Розы  в горшках"


def test_offer_mappings_follows_page_token(monkeypatch):
    monkeypatch.setenv("YANDEX_MARKET_API_TOKEN", "ACMA:x:y")
    monkeypatch.setattr(ym, "PAGE_SIZE", 2)
    pages = [
        {"result": {"offerMappings": [{"offer": {"offerId": "a"}}, {"offer": {"offerId": "b"}}], "paging": {"nextPageToken": "t2"}}},
        {"result": {"offerMappings": [{"offer": {"offerId": "c"}}], "paging": {}}},
    ]
    seen = []

    def handler(method, url, headers, params, json_body):
        assert headers["Api-Key"] == "ACMA:x:y"
        seen.append(params)
        return _resp(200, pages.pop(0))

    monkeypatch.setattr(ym.httpx, "Client", _fake_client(handler))
    rows = ym.YandexMarketClient().offer_mappings(104054570)
    assert [r["offer"]["offerId"] for r in rows] == ["a", "b", "c"]
    assert "page_token" not in (seen[0] or {})
    assert seen[1]["page_token"] == "t2"


def test_slim_card_maps_fields():
    slim = ym.slim_card(
        {
            "offer": {
                "offerId": "Veresk 615",
                "name": "Розовый сад",
                "description": "<p>Розы в горшках</p>",
                "pictures": ["p1.jpg", "p2.jpg"],
                "basicPrice": {"value": 11980.0, "currencyId": "RUR"},
                "cardStatus": "HAS_CARD_CAN_UPDATE",
            },
            "mapping": {"marketModelId": 6092688323, "marketSku": 103243484743},
        },
        {"Veresk 615": 91},
    )
    assert slim["offer_id"] == "Veresk 615"
    assert slim["price"] == "11980.0"
    assert slim["currency"] == "RUB"
    assert slim["is_active"] is True
    assert slim["image"] == "p1.jpg"
    assert slim["images_count"] == 2
    assert slim["content_rating"] == 91
    assert slim["url"] == "https://market.yandex.ru/product/6092688323?sku=103243484743"
    assert slim["description_preview"] == "Розы в горшках"


def test_business_picks_first_with_id(monkeypatch):
    monkeypatch.setenv("YANDEX_MARKET_API_TOKEN", "ACMA:x:y")

    def handler(method, url, headers, params, json_body):
        assert method == "GET" and url.endswith("/campaigns")
        return _resp(
            200,
            {"campaigns": [{"business": {}}, {"business": {"id": 104054570, "name": "Veresk"}}]},
        )

    monkeypatch.setattr(ym.httpx, "Client", _fake_client(handler))
    assert ym.YandexMarketClient().business() == {"id": 104054570, "name": "Veresk"}


def test_reconciliation_adjusted_month_total():
    from plugins.moysklad.yandex_stats import build_reconciliation

    month_report = {
        "2026-07": {
            "yandex_market": {"turnover": 1278924.0, "orders": 102},
            "flowwow": {"turnover": 628388.0, "orders": 51},
            "direct": {"turnover": 123270.0, "orders": 10},
        }
    }
    stats = {"months": {"2026-07": {"orders": 104, "buyer_total": 732178.0, "payout_total": 566663.0}}}
    row = build_reconciliation(month_report, stats)[0]
    assert row["ms_month_total"] == 2030582.0
    # whole month with Yandex re-priced to cabinet buyer totals
    assert row["adjusted_month_total"] == 2030582.0 - 1278924.0 + 732178.0
    assert row["delta_pct"] > 0.7
