"""Конкретные рекомендации по карточкам маркетплейсов (Карточки → монетизация).

Каждый блок опирается на данные площадок (contentRating / фото / цены из
Яндекс Маркет и Flowwow API) — block_meta объясняет правило и источник.
"""

from __future__ import annotations

from plugins.moysklad.cards_recommendations import (
    RATING_THRESHOLD,
    block_meta,
    build_recommendations,
    retrieve_cards,
)


def _card(name: str, **listings) -> dict:
    return {"name": name, "listings": listings}


def test_low_rating_and_few_photos_blocks():
    combined = [
        _card(
            "Букет Veresk 101",
            yandex={
                "content_rating": 40,
                "images_count": 1,
                "is_active": True,
                "price": 3500,
            },
        )
    ]
    rec = build_recommendations(combined)
    low = rec["low_rating"][0]
    assert low["name"] == "Букет Veresk 101"
    assert low["rating"] == 40
    assert "фото" in low["action"] or "контент" in low["action"]
    few = rec["few_photos"][0]
    assert few["images"] == 1
    assert "фото" in few["action"]


def test_cross_marketplace_expansion_blocks():
    combined = [
        _card(
            "Только Flowwow",
            flowwow={"is_active": True, "price": 5000, "images_count": 4},
        ),
        _card(
            "Только Яндекс",
            yandex={
                "is_active": True,
                "price": 4200,
                "images_count": 5,
                "content_rating": 92,
            },
        ),
        _card(
            "Слабый Яндекс",
            yandex={
                "is_active": True,
                "price": 4200,
                "images_count": 5,
                "content_rating": 50,
            },
        ),
    ]
    rec = build_recommendations(combined)
    assert [r["name"] for r in rec["add_to_yandex"]] == ["Только Flowwow"]
    # Низкий рейтинг не рекомендуем тиражировать на Flowwow.
    assert [r["name"] for r in rec["add_to_flowwow"]] == ["Только Яндекс"]


def test_price_gap_duplicates_and_hidden_candidates():
    combined = [
        _card(
            "Пион Veresk 7",
            flowwow={"is_active": True, "price": 100.0, "images_count": 4},
            yandex={
                "is_active": True,
                "price": 150.0,
                "images_count": 4,
                "content_rating": 95,
            },
        ),
        _card(
            "Роза Veresk 9",
            yandex={"is_active": True, "price": 100, "offer_id": "veresk 9"},
        ),
        _card(
            "Роза красная Veresk 9",
            yandex={"is_active": True, "price": 100, "offer_id": "veresk 9"},
        ),
        _card(
            "Скрытая",
            flowwow={
                "is_active": False,
                "is_archived": False,
                "price": 900,
                "images_count": 5,
            },
        ),
    ]
    rec = build_recommendations(combined)
    gap = rec["price_gaps"][0]
    assert gap["name"] == "Пион Veresk 7"
    assert gap["gap_pct"] == round(50 / 150, 3)
    dup = rec["duplicates"][0]
    assert dup["article"] == "Veresk 9"
    assert dup["marketplace"] == "yandex"
    assert len(dup["names"]) == 2
    hidden = rec["hidden_candidates"][0]
    assert hidden["name"] == "Скрытая"
    assert "остатки" in hidden["action"] or "открыть" in hidden["action"]


def test_block_meta_cites_platform_sources():
    meta = block_meta()
    assert set(meta) == {
        "low_rating",
        "few_photos",
        "add_to_yandex",
        "add_to_flowwow",
        "duplicates",
        "price_gaps",
        "hidden_candidates",
    }
    for block in meta.values():
        assert block["rule"]
        assert block["source"]
        # Seller docs / KB — placement & promotion, not LLM fluff.
        assert block.get("docs")
        assert block.get("docs_source")
        assert block.get("docs_action")
    # Рекомендации явно основаны на данных площадок, не выдуманы.
    assert "Яндекс Маркет API" in meta["low_rating"]["source"]
    assert "Flowwow" in meta["few_photos"]["source"]
    assert str(RATING_THRESHOLD) in meta["low_rating"]["rule"]
    assert "Яндекс" in meta["low_rating"]["docs_source"] or "контент" in meta[
        "low_rating"
    ]["docs"].lower()


def test_retrieve_cards_token_overlap():
    combined = [
        {"name": "Букет пионов розовый"},
        {"name": "Роза красная"},
        {"name": ""},
    ]
    got = retrieve_cards(combined, "букет пионов")
    assert got
    assert got[0]["name"] == "Букет пионов розовый"
    # Пустой / низкосигнальный запрос ничего не тянет в контекст чата.
    assert retrieve_cards(combined, "") == []
    assert retrieve_cards(combined, "ok") == []
