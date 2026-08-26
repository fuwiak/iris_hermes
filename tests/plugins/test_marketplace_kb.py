"""Marketplace KB grounding for card recommendations (placement / promo docs)."""

from __future__ import annotations

from plugins.moysklad.cards_recommendations import (
    block_meta,
    build_recommendations,
    recommendations_for_card,
)
from plugins.moysklad.marketplace_kb import (
    ENTRIES,
    format_kb_context,
    kb_entries_for_block,
    knowledge_payload,
    primary_action_for_block,
)


def test_kb_covers_all_recommendation_blocks():
    meta = block_meta()
    for key in meta:
        entries = kb_entries_for_block(key)
        assert entries, f"block {key} must cite marketplace docs"
        assert meta[key].get("docs")
        assert meta[key].get("docs_source")
        assert meta[key].get("docs_action")


def test_kb_entries_cite_yandex_and_flowwow():
    payload = knowledge_payload()
    assert payload["entry_count"] == len(ENTRIES)
    assert "yandex" in payload["marketplaces"]
    assert "flowwow" in payload["marketplaces"]
    assert "general" in payload["marketplaces"]
    # Official seller-help framing, not generic SEO fluff.
    low = kb_entries_for_block("low_rating")[0]
    assert "contentRating" in low["rule"] or "рейтинг" in low["rule"].lower()
    assert "Яндекс" in low["source_label"]
    fw = kb_entries_for_block("add_to_flowwow")[0]
    assert "Flowwow" in fw["source_label"] or "Flowwow" in fw["title"]


def test_build_recommendations_actions_include_kb_tips():
    combined = [
        {
            "name": "Букет Veresk 1",
            "listings": {
                "yandex": {
                    "content_rating": 40,
                    "images_count": 1,
                    "is_active": True,
                    "price": 1000,
                }
            },
        }
    ]
    rec = build_recommendations(combined)
    action = rec["low_rating"][0]["action"]
    tip = primary_action_for_block("low_rating")
    assert tip
    assert tip in action or "характеристик" in action


def test_recommendations_for_card_concrete_and_docs_backed():
    row = {
        "name": "Пионы",
        "listings": {
            "flowwow": {"is_active": True, "price": 5000, "images_count": 4}
        },
    }
    actions = recommendations_for_card(row)
    assert actions
    assert any(a["block"] == "add_to_yandex" for a in actions)
    first = actions[0]
    assert first["name"] == "Пионы"
    assert first["action"]
    assert first["docs"]
    assert first["docs_source"]


def test_get_cards_recommendations_name_returns_per_card(monkeypatch):
    """HTTP path: ?name=… → recommendations_for_card (monetization per card)."""
    combined = [
        {
            "name": "Пионы",
            "listings": {
                "flowwow": {"is_active": True, "price": 5000, "images_count": 4}
            },
        }
    ]

    def _fake_payload(**_kwargs):
        return {"combined": combined, "generated_at": "2026-01-01T00:00:00Z"}

    monkeypatch.setattr(
        "plugins.moysklad.marketplace_cards.marketplace_cards_payload",
        _fake_payload,
    )
    from plugins.moysklad.dashboard.plugin_api import get_cards_recommendations

    payload = get_cards_recommendations(
        force=False,
        rating_threshold=85,
        min_photos=3,
        price_gap_min=0.10,
        cap=25,
        name="Пионы",
    )
    assert payload["ok"] is True
    assert payload["found"] is True
    assert payload["name"] == "Пионы"
    assert payload["recommendations"]
    assert payload["recommendations"][0]["docs"]
    assert "knowledge" in payload
    assert "meta" in payload
    # Aggregate blocks stay off the per-card response.
    assert "low_rating" not in payload

    missing = get_cards_recommendations(
        force=False,
        rating_threshold=85,
        min_photos=3,
        price_gap_min=0.10,
        cap=25,
        name="Нет такой",
    )
    assert missing["found"] is False
    assert missing["recommendations"] == []


def test_format_kb_context_for_advisor_prompt():
    text = format_kb_context()
    assert "База знаний площадок" in text
    assert "Яндекс" in text or "yandex" in text.lower()
    assert "Flowwow" in text or "flowwow" in text.lower()
