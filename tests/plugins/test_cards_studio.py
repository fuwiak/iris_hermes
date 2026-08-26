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


def test_photo_shot_plan_for_postcard_and_bouquet():
    card_shots = studio.photo_shot_plan(
        'Открытка "Люблю!", для любимой', images_count=2
    )
    assert len(card_shots) >= 2
    assert any("открыт" in (s["title"] + s["prompt"]).lower() or "card" in s["prompt"].lower() for s in card_shots)
    assert all(s["prompt"] and s["title"] and s["why"] for s in card_shots)

    bouquet = studio.photo_shot_plan("Букет пионов", images_count=0)
    assert len(bouquet) >= 3
    assert "bouquet" in bouquet[0]["prompt"].lower() or "букет" in bouquet[0]["prompt"].lower()


def test_improve_card_content_returns_description_and_shots(monkeypatch):
    module = types.ModuleType("agent.auxiliary_client")
    module.call_llm = lambda **kw: {
        "text": "[FLOWWOW]\nТёплый текст\n[YANDEX]\nСтруктура для Маркета"
    }
    module.extract_content_or_reasoning = lambda resp: resp["text"]
    monkeypatch.setitem(sys.modules, "agent.auxiliary_client", module)

    out = studio.improve_card_content(
        name='Открытка "Люблю!" Veresk 749',
        images_count=2,
        content_rating=67,
        marketplace="yandex",
        generate_images=False,
    )
    assert out["ok"] is True
    assert out["description"]["ok"] is True
    assert "Структура" in out["description"]["preferred"]
    assert out["photos"]["shots"]
    assert out["photos"]["have"] == 2
    assert out["photos"]["skipped"] is True
    assert out["photos"]["generated"] == []


def test_generate_card_photos_without_provider(monkeypatch):
    monkeypatch.setattr(
        "agent.image_gen_registry.get_active_provider",
        lambda: None,
        raising=False,
    )
    # Ensure import path resolves even if registry not loaded.
    import agent.image_gen_registry as reg

    monkeypatch.setattr(reg, "get_active_provider", lambda: None)
    out = studio.generate_card_photos(
        [{"id": "hero", "title": "Герой", "prompt": "test prompt"}],
        max_images=1,
    )
    assert out["ok"] is False
    assert out["error"] == "image_gen_not_configured"


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
