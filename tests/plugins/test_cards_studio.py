"""«Карточки» sub-tab backend: MS search, draft sections, orders sort."""

from __future__ import annotations

import sys
import types

import plugins.moysklad.cards_studio as studio


def test_search_ms_assortment_slims_rows(monkeypatch):
    import plugins.moysklad.client as ms

    class _FakeClient:
        def _request(self, method, path, params=None, **_kw):
            assert path == "/entity/assortment"
            assert params["search"] == "букет"
            return {
                "rows": [
                    {
                        "id": "abc",
                        "name": "Букет пионов",
                        "meta": {"type": "bundle"},
                        "salePrices": [{"value": 1699000.0}],
                    }
                ]
            }

    monkeypatch.setattr(ms, "MoySkladClient", _FakeClient)
    rows = studio.search_ms_assortment("букет")
    assert rows == [
        {"id": "abc", "type": "bundle", "name": "Букет пионов", "price": 16990.0, "archived": False}
    ]
    assert studio.search_ms_assortment("") == []


def test_split_draft_sections():
    text = "[FLOWWOW]\nТёплый текст.\n[YANDEX]\nСтруктурный текст.\n- плюс"
    sections = studio._split_draft_sections(text)
    assert sections["flowwow"] == "Тёплый текст."
    assert sections["yandex_market"].startswith("Структурный текст.")
    fallback = studio._split_draft_sections("просто текст")
    assert fallback["flowwow"] == "просто текст"


def test_generate_card_draft_requires_name_and_parses(monkeypatch):
    assert studio.generate_card_draft(name="")["ok"] is False

    module = types.ModuleType("agent.auxiliary_client")
    module.call_llm = lambda **kw: {"text": "[FLOWWOW]\nA\n[YANDEX]\nB"}
    module.extract_content_or_reasoning = lambda resp: resp["text"]
    monkeypatch.setitem(sys.modules, "agent.auxiliary_client", module)
    out = studio.generate_card_draft(name="Букет", price=5990.0)
    assert out["ok"] is True
    assert out["drafts"] == {"flowwow": "A", "yandex_market": "B"}


def test_recent_orders_sorted_newest_first(monkeypatch):
    import plugins.moysklad.yandex_market as ym

    class _FakeClient:
        def campaigns(self):
            return [{"id": 1, "domain": "Сокольники"}]

        def _request(self, method, path, params=None, **_kw):
            return {
                "orders": [
                    {"id": 1, "status": "DELIVERED", "creationDate": "21-08-2026 10:00:00", "buyerTotal": 100, "items": []},
                    {"id": 2, "status": "PROCESSING", "creationDate": "23-08-2026 09:00:00", "buyerTotal": 200, "items": []},
                ]
            }

    monkeypatch.setattr(ym, "token_configured", lambda: True)
    monkeypatch.setattr(ym, "YandexMarketClient", _FakeClient)
    out = studio.recent_yandex_orders(limit=5)
    assert out["configured"] is True
    assert [o["id"] for o in out["orders"]] == [2, 1]
