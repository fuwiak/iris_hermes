"""Flowwow HTTP client: auth, POST endpoints, pagination, retry, health probe."""

from __future__ import annotations

import json

import plugins.flowwow.client as fw


def _resp(status: int, body: dict, *, text: str = ""):
    class _Resp:
        status_code = status
        content = json.dumps(body).encode() if body else b""

        def json(self):
            return json.loads(self.content) if self.content else {}

    r = _Resp()
    r.text = text or json.dumps(body)
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
    monkeypatch.delenv("FLOWWOW_API_TOKEN", raising=False)
    try:
        fw.FlowwowClient()
        assert False, "expected FlowwowError"
    except fw.FlowwowError as exc:
        assert "FLOWWOW_API_TOKEN" in str(exc)


def test_health_pings_then_posts_shops(monkeypatch):
    monkeypatch.setenv("FLOWWOW_API_TOKEN", "tok-123")
    monkeypatch.delenv("FLOWWOW_API_URL", raising=False)
    monkeypatch.setenv("FLOWWOW_REQUEST_DELAY_MS", "0")
    calls = []

    def handler(method, url, headers, params, json_body):
        calls.append((method, url, headers, params, json_body))
        if url.endswith("/apiseller/ping/check"):
            return _resp(200, {"say": "hello"})
        return _resp(
            200,
            {"shops": [{"shopId": 32992, "name": "Вереск"}], "total": 1},
        )

    monkeypatch.setattr(fw.httpx, "Client", _fake_client(handler))

    out = fw.FlowwowClient().health()
    assert out["ok"] is True
    assert out["ping"] == "hello"
    assert out["sample_shop"] == {"shopId": 32992, "name": "Вереск"}
    assert out["base_url"] == fw.DEFAULT_BASE

    ping, shops = calls
    assert ping[0] == "GET"
    assert ping[1] == f"{fw.DEFAULT_BASE}/apiseller/ping/check"
    assert shops[0] == "POST"
    assert shops[1] == f"{fw.DEFAULT_BASE}/apiseller/shops"
    assert shops[2]["Authorization"] == "Bearer tok-123"
    assert shops[4] == {"status": "active", "limit": 1}


def test_base_url_override(monkeypatch):
    monkeypatch.setenv("FLOWWOW_API_TOKEN", "tok-123")
    monkeypatch.setenv("FLOWWOW_API_URL", "https://custom.example/api/")
    monkeypatch.setenv("FLOWWOW_REQUEST_DELAY_MS", "0")

    def handler(method, url, headers, params, json_body):
        assert url.startswith("https://custom.example/api/apiseller/")
        if url.endswith("/ping/check"):
            return _resp(200, {"say": "hello"})
        return _resp(200, {"shops": [], "total": 0})

    monkeypatch.setattr(fw.httpx, "Client", _fake_client(handler))
    fw.FlowwowClient().health()


def test_shops_paginates_until_total(monkeypatch):
    monkeypatch.setenv("FLOWWOW_API_TOKEN", "tok-123")
    monkeypatch.setenv("FLOWWOW_REQUEST_DELAY_MS", "0")
    monkeypatch.setattr(fw, "SHOPS_PAGE_SIZE", 2)
    pages = [
        {"shops": [{"shopId": 1}, {"shopId": 2}], "total": 3},
        {"shops": [{"shopId": 3}], "total": 3},
    ]
    seen_bodies = []

    def handler(method, url, headers, params, json_body):
        seen_bodies.append(json_body)
        return _resp(200, pages.pop(0))

    monkeypatch.setattr(fw.httpx, "Client", _fake_client(handler))
    out = fw.FlowwowClient().shops(status="active")
    assert [r["shopId"] for r in out["rows"]] == [1, 2, 3]
    assert out["total"] == 3
    assert seen_bodies[0]["page"] == 0 and seen_bodies[1]["page"] == 1
    assert all(b["status"] == "active" for b in seen_bodies)


def test_products_passes_shop_id_query_and_trims_limit(monkeypatch):
    monkeypatch.setenv("FLOWWOW_API_TOKEN", "tok-123")
    monkeypatch.setenv("FLOWWOW_REQUEST_DELAY_MS", "0")
    monkeypatch.setattr(fw, "PRODUCTS_PAGE_SIZE", 2)
    pages = [
        {"items": [{"productId": 1}, {"productId": 2}], "total": 5},
        {"items": [{"productId": 3}, {"productId": 4}], "total": 5},
    ]
    seen = []

    def handler(method, url, headers, params, json_body):
        seen.append((method, url, params, json_body))
        return _resp(200, pages.pop(0))

    monkeypatch.setattr(fw.httpx, "Client", _fake_client(handler))
    out = fw.FlowwowClient().products(32992, limit=3)
    assert [r["productId"] for r in out["rows"]] == [1, 2, 3]
    method, url, params, body = seen[0]
    assert method == "POST"
    assert url.endswith("/apiseller/products")
    assert params == {"shopId": 32992}
    assert body["withArchive"] is False and body["extended"] is False


def test_retries_on_429_then_succeeds(monkeypatch):
    monkeypatch.setenv("FLOWWOW_API_TOKEN", "tok-123")
    monkeypatch.setenv("FLOWWOW_REQUEST_DELAY_MS", "0")
    monkeypatch.setattr(fw.time, "sleep", lambda s: None)
    attempts = {"n": 0}

    def handler(method, url, headers, params, json_body):
        attempts["n"] += 1
        if attempts["n"] < 3:
            return _resp(429, {}, text="rate limited")
        return _resp(200, {"say": "hello"})

    monkeypatch.setattr(fw.httpx, "Client", _fake_client(handler))
    out = fw.FlowwowClient().ping()
    assert attempts["n"] == 3
    assert out == {"say": "hello"}


def test_4xx_raises_flowwow_error(monkeypatch):
    monkeypatch.setenv("FLOWWOW_API_TOKEN", "tok-123")
    monkeypatch.setenv("FLOWWOW_REQUEST_DELAY_MS", "0")

    def handler(method, url, headers, params, json_body):
        return _resp(401, {}, text="Недействительный токен авторизации")

    monkeypatch.setattr(fw.httpx, "Client", _fake_client(handler))
    try:
        fw.FlowwowClient().shops()
        assert False, "expected FlowwowError"
    except fw.FlowwowError as exc:
        assert exc.status_code == 401
