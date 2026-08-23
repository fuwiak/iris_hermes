"""Deterministic card recommendations + retrieval (the chat's RAG layer)."""

from __future__ import annotations

from plugins.moysklad.cards_recommendations import build_recommendations, retrieve_cards


def _card(name, mp, **product):
    product.setdefault("is_active", True)
    return {"name": name, "marketplaces": [mp], "listings": {mp: product}}


def test_low_rating_and_few_photos_flagged():
    combined = [
        _card("Открытка", "yandex_market", content_rating=67, images_count=2),
        _card("Букет", "yandex_market", content_rating=95, images_count=6),
    ]
    rec = build_recommendations(combined)
    assert [r["name"] for r in rec["low_rating"]] == ["Открытка"]
    assert rec["low_rating"][0]["rating"] == 67
    assert [r["name"] for r in rec["few_photos"]] == ["Открытка"]


def test_single_marketplace_cards_become_add_candidates():
    combined = [
        _card("Пионы", "flowwow", price="8990.00", images_count=6),
        _card("Розы в горшках", "yandex_market", price="11980.00", content_rating=91),
        _card("Слабая карта", "yandex_market", price="500.00", content_rating=60),
    ]
    rec = build_recommendations(combined)
    assert [r["name"] for r in rec["add_to_yandex"]] == ["Пионы"]
    # only healthy-rating yandex cards are suggested for Flowwow
    assert [r["name"] for r in rec["add_to_flowwow"]] == ["Розы в горшках"]


def test_price_gap_and_duplicates():
    both = {
        "name": "Букет 101 роза",
        "marketplaces": ["flowwow", "yandex_market"],
        "listings": {
            "flowwow": {"price": "110890.00", "is_active": True},
            "yandex_market": {"price": "89980.00", "is_active": True},
        },
    }
    dup_a = _card("Букет роз. Veresk 325", "flowwow", price="16489.00")
    dup_b = _card("Букет премиальных роз. Veresk 325", "flowwow", price="15890.00")
    rec = build_recommendations([both, dup_a, dup_b])
    gap = rec["price_gaps"][0]
    assert gap["name"] == "Букет 101 роза"
    assert gap["gap_pct"] > 0.1
    dup = rec["duplicates"][0]
    assert dup["article"] == "Veresk 325"
    assert len(dup["names"]) == 2


def test_hidden_candidate_needs_content_and_price():
    ready = _card("Скрытый хит", "flowwow", is_active=False, images_count=5, price="5990.00")
    bare = _card("Скрытый пустой", "flowwow", is_active=False, images_count=1, price="100.00")
    rec = build_recommendations([ready, bare])
    assert [r["name"] for r in rec["hidden_candidates"]] == ["Скрытый хит"]


def test_retrieve_cards_ranks_by_token_overlap():
    combined = [
        _card("101 пионовидные розовые розы в корзине", "flowwow"),
        _card("Гладиолусы коралловые", "yandex_market"),
        _card("Пионовидные розы, размер L", "flowwow"),
    ]
    hits = retrieve_cards(combined, "пионовидные розы в корзине", k=2)
    assert hits[0]["name"].startswith("101 пионовидные")
    assert len(hits) == 2
    assert retrieve_cards(combined, "", k=5) == []


def test_params_are_modelable():
    combined = [
        _card("A", "yandex_market", content_rating=90, images_count=4),
        _card("B", "yandex_market", content_rating=80, images_count=4),
    ]
    strict = mc_build = build_recommendations(combined, rating_threshold=95)
    assert {r["name"] for r in strict["low_rating"]} == {"A", "B"}
    loose = build_recommendations(combined, rating_threshold=75)
    assert loose["low_rating"] == []
    photos = build_recommendations(combined, min_photos=5)
    assert {r["name"] for r in photos["few_photos"]} == {"A", "B"}
    assert mc_build is strict


def test_block_meta_reflects_params():
    from plugins.moysklad.cards_recommendations import block_meta

    meta = block_meta(rating_threshold=90, min_photos=5, price_gap_min=0.2)
    assert "90" in meta["low_rating"]["rule"]
    assert "5" in meta["few_photos"]["rule"]
    assert "20%" in meta["price_gaps"]["rule"]
    assert all("source" in v and v["source"] for v in meta.values())
