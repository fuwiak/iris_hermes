"""Elasticsearch cache talks REST; no live cluster in tests."""

from __future__ import annotations

from plugins.moysklad import es_cache


def test_es_get_skips_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("ELASTICSEARCH_URL", raising=False)
    monkeypatch.delenv("MOYSKLAD_ELASTICSEARCH_URL", raising=False)
    es_cache.reset_for_tests()
    assert es_cache.enabled() is False
    assert es_cache.es_get("anything") is None


def test_es_put_stores_payload_blob(monkeypatch) -> None:
    captured: dict = {}

    def fake_request(method, path, body=None, timeout=3.0):
        if path == "/":
            return 200, {"tagline": "ok"}
        if method == "HEAD":
            return 404, {}
        if method == "PUT" and path == f"/{es_cache.INDEX}":
            captured["mapping"] = body
            return 200, {}
        if method == "PUT" and "_doc" in path:
            captured["doc"] = body
            return 201, {}
        return 0, None

    monkeypatch.setenv("ELASTICSEARCH_URL", "http://localhost:9200")
    es_cache.reset_for_tests()
    monkeypatch.setattr(es_cache, "_request", fake_request)
    ok = es_cache.es_put(
        "moysklad:catalog:test",
        {"synced_at": 1.0, "catalog": {"rows": [{"id": "c1"}]}},
        kind="catalog",
    )
    assert ok is True
    assert captured["mapping"]["mappings"]["properties"]["payload"]["enabled"] is False
    assert captured["doc"]["payload"]["catalog"]["rows"][0]["id"] == "c1"
    assert captured["doc"]["kind"] == "catalog"


def test_es_get_reads_source_payload(monkeypatch) -> None:
    def fake_request(method, path, body=None, timeout=3.0):
        if path == "/":
            return 200, {}
        if method == "GET" and "_doc" in path:
            return 200, {"_source": {"payload": {"synced_at": 9, "summary": {"ok": True}}}}
        return 0, None

    monkeypatch.setenv("ELASTICSEARCH_URL", "http://es.example:9200")
    es_cache.reset_for_tests()
    monkeypatch.setattr(es_cache, "_request", fake_request)
    env = es_cache.es_get("dash-key")
    assert env is not None
    assert env["summary"]["ok"] is True
