"""Deterministic card recommendations + retrieval for the «Карточки» chat.

Everything here is computed in pure Python over the cached combined card
list — milliseconds, no LLM. The chat uses it as its retrieval layer
(RAG-lite): the model receives only the cards relevant to the question and
the precomputed recommendation blocks, then phrases them — it does not
invent lists of its own.

Blocks:
- ``low_rating``       — Yandex contentRating below the threshold
- ``few_photos``       — active cards with < 3 photos (per marketplace)
- ``add_to_yandex``    — Flowwow-only active cards (Yandex sells more)
- ``add_to_flowwow``   — Yandex-only active cards with a healthy rating
- ``duplicates``       — same «Veresk NNN» article on 2+ cards of one market
- ``price_gaps``       — same card on both markets, prices differ > 10%
- ``hidden_candidates``— hidden cards that look sellable (photos + price)
"""

from __future__ import annotations

import re
from typing import Any

RATING_THRESHOLD = 85
PRICE_GAP_MIN = 0.10
_ARTICLE_RE = re.compile(r"veresk\s*(\d+)", re.IGNORECASE)
_WORD_RE = re.compile(r"[0-9a-zа-яё]+", re.IGNORECASE)


def _price(product: dict[str, Any]) -> float:
    try:
        return float(product.get("price") or 0)
    except (TypeError, ValueError):
        return 0.0


def _article(product: dict[str, Any], name: str) -> str:
    for source in (str(product.get("offer_id") or ""), name):
        match = _ARTICLE_RE.search(source)
        if match:
            return match.group(1)
    return ""


def build_recommendations(
    combined: list[dict[str, Any]],
    *,
    rating_threshold: int = RATING_THRESHOLD,
    cap: int = 25,
) -> dict[str, list[dict[str, Any]]]:
    low_rating: list[dict[str, Any]] = []
    few_photos: list[dict[str, Any]] = []
    add_to_yandex: list[dict[str, Any]] = []
    add_to_flowwow: list[dict[str, Any]] = []
    price_gaps: list[dict[str, Any]] = []
    hidden_candidates: list[dict[str, Any]] = []
    by_article: dict[tuple[str, str], list[str]] = {}

    for row in combined or []:
        name = str(row.get("name") or "")
        listings: dict[str, dict[str, Any]] = row.get("listings") or {}
        marketplaces = sorted(listings)

        for marketplace, product in listings.items():
            rating = product.get("content_rating")
            if rating is not None and rating < rating_threshold:
                low_rating.append(
                    {
                        "name": name,
                        "marketplace": marketplace,
                        "rating": rating,
                        "images": product.get("images_count"),
                        "action": "поднять контент: фото/описание/характеристики",
                    }
                )
            images = product.get("images_count")
            if (
                images is not None
                and images < 3
                and product.get("is_active")
                and not product.get("is_archived")
            ):
                few_photos.append(
                    {
                        "name": name,
                        "marketplace": marketplace,
                        "images": images,
                        "action": "добавить 5–6 фото",
                    }
                )
            article = _article(product, name)
            if article:
                by_article.setdefault((marketplace, article), []).append(name)

        active_anywhere = any(
            p.get("is_active") and not p.get("is_archived") for p in listings.values()
        )
        if len(marketplaces) == 1 and active_anywhere:
            product = listings[marketplaces[0]]
            entry = {
                "name": name,
                "price": _price(product) or None,
                "images": product.get("images_count"),
            }
            if marketplaces[0] == "flowwow":
                add_to_yandex.append(entry)
            else:
                rating = product.get("content_rating")
                if rating is None or rating >= rating_threshold:
                    entry["rating"] = rating
                    add_to_flowwow.append(entry)

        if len(marketplaces) >= 2:
            prices = {mp: _price(p) for mp, p in listings.items() if _price(p) > 0}
            if len(prices) >= 2:
                low, high = min(prices.values()), max(prices.values())
                if high > 0 and (high - low) / high > PRICE_GAP_MIN:
                    price_gaps.append(
                        {
                            "name": name,
                            "prices": {mp: round(v, 2) for mp, v in prices.items()},
                            "gap_pct": round((high - low) / high, 3),
                            "action": "выровнять цены или подтвердить разницу",
                        }
                    )

        if (
            not active_anywhere
            and listings
            and all(not p.get("is_archived") for p in listings.values())
        ):
            product = listings[marketplaces[0]]
            if (product.get("images_count") or 0) >= 3 and _price(product) > 0:
                hidden_candidates.append(
                    {
                        "name": name,
                        "marketplace": marketplaces[0],
                        "price": _price(product),
                        "action": "скрыта, контент готов — проверить остатки и открыть",
                    }
                )

    duplicates = [
        {
            "marketplace": marketplace,
            "article": f"Veresk {article}",
            "names": sorted(set(names))[:4],
            "action": "один артикул на нескольких карточках — объединить",
        }
        for (marketplace, article), names in sorted(by_article.items())
        if len(set(names)) > 1
    ]

    add_to_yandex.sort(key=lambda r: -(r.get("price") or 0))
    add_to_flowwow.sort(key=lambda r: -(r.get("rating") or 0))
    low_rating.sort(key=lambda r: r.get("rating") or 0)
    return {
        "low_rating": low_rating[:cap],
        "few_photos": few_photos[:cap],
        "add_to_yandex": add_to_yandex[:cap],
        "add_to_flowwow": add_to_flowwow[:cap],
        "duplicates": duplicates[:cap],
        "price_gaps": price_gaps[:cap],
        "hidden_candidates": hidden_candidates[:cap],
    }


def _tokens(text: str) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(text or "") if len(w) > 2}


def retrieve_cards(
    combined: list[dict[str, Any]],
    query: str,
    *,
    k: int = 40,
) -> list[dict[str, Any]]:
    """Token-overlap retrieval over card names — the chat's search layer.

    No embeddings server, no latency: scoring ~600 rows takes microseconds
    and behaves like a keyword search. Empty/low-signal queries return [].
    """
    query_tokens = _tokens(query)
    if not query_tokens:
        return []
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in combined or []:
        name_tokens = _tokens(str(row.get("name") or ""))
        if not name_tokens:
            continue
        overlap = len(query_tokens & name_tokens)
        if overlap:
            scored.append((overlap / len(query_tokens), row))
    scored.sort(key=lambda pair: -pair[0])
    return [row for _score, row in scored[:k]]
