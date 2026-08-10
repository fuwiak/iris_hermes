"""Flowwow HTTP client: auth, pagination, retry, health probe."""

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


def test_health_uses_bearer_and_base_url(monkeypatch):
    monkeypatch.setenv("FLOWWOW_API_TOKEN", "tok-123")
    monkeypatch.delenv("FLOWWOW_API_URL", raising=False)
    monkeypatch.setenv("FLOWWOW_REQUEST_DELAY_MS", "0")
    calls = []

    def handler(method, url, headers, params, json_body):
        calls.append((method, url, headers, params))
        return _resp(200, {"data": [{"id": "ord-1"}], "total": 1})

    monkeypatch.setattr(fw.httpx, "Client", _fake_client(handler))

    client = fw.FlowwowClient()
    out = client.health()
    assert out["ok"] is True
    assert out["sample_order_id"] == "ord-1"
    assert out["base_url"] == fw.DEFAULT_BASE

    method, url, headers, params = calls[0]
    assert method == "GET"
    assert url == f"{fw.DEFAULT_BASE}/orders"
    assert headers["Authorization"] == "Bearer tok-123"
    assert params == {"limit": 1, "offset": 0}


def test_base_url_override(monkeypatch):
    monkeypatch.setenv("FLOWWOW_API_TOKEN", "tok-123")
    monkeypatch.setenv("FLOWWOW_API_URL", "https://custom.example/api/v2/")
    monkeypatch.setenv("FLOWWOW_REQUEST_DELAY_MS", "0")

    def handler(method, url, headers, params, json_body):
        assert url.startswith("https://custom.example/api/v2/")
        return _resp(200, {"data": []})

    monkeypatch.setattr(fw.httpx, "Client", _fake_client(handler))
    fw.FlowwowClient().health()


def test_fetch_all_paginates_and_dedupes(monkeypatch):
    monkeypatch.setenv("FLOWWOW_API_TOKEN", "tok-123")
    monkeypatch.setenv("FLOWWOW_REQUEST_DELAY_MS", "0")
    monkeypatch.setattr(fw, "PAGE_SIZE", 2)
    pages = [
        {"data": [{"id": "1"}, {"id": "2"}]},
        {"data": [{"id": "2"}, {"id": "3"}]},  # overlapping id — must dedupe
        {"data": []},
    ]

    def handler(method, url, headers, params, json_body):
        return _resp(200, pages.pop(0))

    monkeypatch.setattr(fw.httpx, "Client", _fake_client(handler))
    client = fw.FlowwowClient()
    rows = client.fetch_all("/clients")
    assert [r["id"] for r in rows] == ["1", "2", "3"]


def test_retries_on_429_then_succeeds(monkeypatch):
    monkeypatch.setenv("FLOWWOW_API_TOKEN", "tok-123")
    monkeypatch.setenv("FLOWWOW_REQUEST_DELAY_MS", "0")
    monkeypatch.setattr(fw.time, "sleep", lambda s: None)
    attempts = {"n": 0}

    def handler(method, url, headers, params, json_body):
        attempts["n"] += 1
        if attempts["n"] < 3:
            return _resp(429, {}, text="rate limited")
        return _resp(200, {"data": [{"id": "ok"}]})

    monkeypatch.setattr(fw.httpx, "Client", _fake_client(handler))
    rows, _total = fw.FlowwowClient().get_page("/orders")
    assert attempts["n"] == 3
    assert rows == [{"id": "ok"}]


def test_4xx_raises_flowwow_error(monkeypatch):
    monkeypatch.setenv("FLOWWOW_API_TOKEN", "tok-123")
    monkeypatch.setenv("FLOWWOW_REQUEST_DELAY_MS", "0")

    def handler(method, url, headers, params, json_body):
        return _resp(401, {}, text="unauthorized")

    monkeypatch.setattr(fw.httpx, "Client", _fake_client(handler))
    try:
        fw.FlowwowClient().get_page("/orders")
        assert False, "expected FlowwowError"
    except fw.FlowwowError as exc:
        assert exc.status_code == 401


def test_orders_status_filter_passed_through(monkeypatch):
    monkeypatch.setenv("FLOWWOW_API_TOKEN", "tok-123")
    monkeypatch.setenv("FLOWWOW_REQUEST_DELAY_MS", "0")
    seen_params = []

    def handler(method, url, headers, params, json_body):
        seen_params.append(params)
        return _resp(200, {"data": []})

    monkeypatch.setattr(fw.httpx, "Client", _fake_client(handler))
    fw.FlowwowClient().orders(status="paid", limit=10)
    assert seen_params[0]["status"] == "paid"
