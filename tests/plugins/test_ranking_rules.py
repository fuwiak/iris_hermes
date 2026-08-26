"""Marketplace Ranking Engine: rulebook coverage + per-card audit findings."""

from __future__ import annotations

from plugins.moysklad.ranking_rules import (
    RANKING_RULEBOOK,
    evaluate_card,
    seo_audit,
)


def _card(name, **listings):
    return {"name": name, "listings": listings, "marketplaces": list(listings)}


def test_rulebook_has_both_marketplaces_and_sources():
    marketplaces = {r["marketplace"] for r in RANKING_RULEBOOK}
    assert marketplaces == {"flowwow", "yandex_market"}
    for rule in RANKING_RULEBOOK:
        assert rule["source"].startswith("https://")
        assert rule["factor"]
        assert rule["weight"]


def test_yandex_weights_match_published_percentages():
    weights = {r["id"]: r["weight"] for r in RANKING_RULEBOOK if r["marketplace"] == "yandex_market"}
    assert weights["ym_relevance"] == "27,5%"
    assert weights["ym_affinity"] == "25,5%"
    assert weights["ym_price"] == "18%"
    assert weights["ym_popularity"] == "17%"
    assert weights["ym_rating_reviews"] == "7,5%"
    assert weights["ym_delivery"] == "4,5%"


def test_hidden_flowwow_card_flags_visibility_high_priority():
    card = _card("Розы", flowwow={"is_active": False, "is_archived": False, "images_count": 5})
    findings = evaluate_card(card)
    ids = {f["rule_id"] for f in findings}
    assert "fw_visibility" in ids
    hit = next(f for f in findings if f["rule_id"] == "fw_visibility")
    assert hit["priority"] == "high"
    assert hit["source"].startswith("https://flowwow.com")


def test_few_photos_flags_ctr_loop_rule():
    card = _card("Тюльпаны 25 шт", flowwow={"is_active": True, "images_count": 1})
    findings = evaluate_card(card, min_photos_fw=4)
    hit = next(f for f in findings if f["rule_id"] == "fw_photo_ctr")
    assert "1 фото" in hit["problem"]
    assert "CTR" in hit["factor"]


def test_short_title_without_digits_flags_relevance_on_yandex():
    card = _card(
        "Букет роз",
        yandex_market={"is_active": True, "images_count": 6, "content_rating": 90},
    )
    findings = evaluate_card(card, min_title_len=25)
    hit = next(f for f in findings if f["rule_id"] == "ym_relevance" and "Название" in f["problem"])
    assert hit["weight"] == "27,5%"
    assert "без количества/размера" in hit["problem"]


def test_low_content_rating_flags_card_quality_with_expected_uplift():
    card = _card(
        "Букет из 25 роз 50 см",
        yandex_market={"is_active": True, "images_count": 6, "content_rating": 60},
    )
    findings = evaluate_card(card, quality_threshold=80)
    hit = next(f for f in findings if f["rule_id"] == "ym_card_quality" and "рейтинг" in f["problem"].lower())
    assert hit["priority"] == "high"
    assert "+50%" in hit["expected"]


def test_price_gap_between_marketplaces_flags_price_rule():
    card = _card(
        "Букет из 25 роз 50 см",
        flowwow={"is_active": True, "images_count": 6, "price": "3000"},
        yandex_market={"is_active": True, "images_count": 6, "content_rating": 90, "price": "4000"},
    )
    findings = evaluate_card(card, price_gap_min=0.10)
    hit = next(f for f in findings if f["rule_id"] == "ym_price")
    assert "33%" in hit["problem"]
    assert hit["weight"] == "18%"


def test_clean_card_has_no_findings():
    card = _card(
        "Букет из 25 красных роз 50 см в упаковке",
        flowwow={
            "is_active": True,
            "images_count": 6,
            "description": "х" * 400,
        },
        yandex_market={
            "is_active": True,
            "images_count": 6,
            "content_rating": 95,
            "description": "х" * 400,
            "price": "4000",
        },
    )
    card["listings"]["flowwow"]["price"] = "3900"
    findings = evaluate_card(card)
    assert findings == []


def test_findings_sorted_high_before_medium():
    card = _card("Ромашки", flowwow={"is_active": False, "images_count": 1})
    findings = evaluate_card(card, min_photos_fw=4, min_title_len=25)
    priorities = [f["priority"] for f in findings]
    assert priorities == sorted(priorities, key=lambda p: {"high": 0, "medium": 1, "low": 2}[p])


def test_seo_audit_ranks_worst_cards_first_and_returns_rulebook():
    bad = _card("Ромашки", flowwow={"is_active": False, "images_count": 1})
    good = _card(
        "Букет из 25 красных роз 50 см",
        flowwow={"is_active": True, "images_count": 6, "description": "х" * 400, "price": "3900"},
    )
    result = seo_audit([bad, good], cap=10)
    assert result["rulebook"] == RANKING_RULEBOOK
    assert result["cards_total"] == 2
    assert result["cards"][0]["name"] == "Ромашки"
    assert result["cards"][0]["high"] >= 1


def test_archived_card_skipped_entirely():
    card = _card("Ромашки", flowwow={"is_active": False, "is_archived": True, "images_count": 1})
    assert evaluate_card(card) == []
