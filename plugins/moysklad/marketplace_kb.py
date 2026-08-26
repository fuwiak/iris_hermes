"""Seller-docs / knowledge-base grounding for marketplace card recommendations.

Curated excerpts from official seller help (Яндекс Маркет, Flowwow) plus
general marketplace practice. Used so recommendation *actions* cite a real
placement/promotion rule — not generic «улучшите SEO» fluff.

Sources are public seller help / partner docs; text is paraphrased for the
operator UI and LLM advisor context.
"""

from __future__ import annotations

from typing import Any

# block_key → kb entry ids (see ENTRIES)
_BLOCK_KB: dict[str, tuple[str, ...]] = {
    "low_rating": ("yandex_content_rating", "yandex_card_attributes", "general_photos"),
    "few_photos": ("general_photos", "flowwow_photos", "yandex_content_rating"),
    "add_to_yandex": ("yandex_listing_basics", "yandex_boost", "general_cross_market"),
    "add_to_flowwow": ("flowwow_listing_basics", "flowwow_showcase", "general_cross_market"),
    "duplicates": ("general_sku_hygiene", "yandex_listing_basics"),
    "price_gaps": ("general_price_parity", "yandex_boost", "flowwow_listing_basics"),
    "hidden_candidates": ("flowwow_visibility", "yandex_listing_basics", "general_stock"),
}

ENTRIES: dict[str, dict[str, str]] = {
    "yandex_content_rating": {
        "marketplace": "yandex",
        "topic": "content",
        "title": "Контент-рейтинг карточки",
        "rule": (
            "В кабинете Яндекс Маркета у каждой карточки есть contentRating "
            "(0–100). Низкий рейтинг снижает показы в поиске и каталоге."
        ),
        "action": (
            "Заполнить все обязательные характеристики, добавить 5–6 фото "
            "(основное + ракурсы + в интерьере), расширить описание и "
            "перепроверить карточку в «Качестве карточек»."
        ),
        "source_label": "Яндекс Маркет — справка продавца: качество / контент карточки",
        "source_url": "https://yandex.ru/support/marketplace/",
    },
    "yandex_card_attributes": {
        "marketplace": "yandex",
        "topic": "placement",
        "title": "Характеристики и категория",
        "rule": (
            "Карточка должна стоять в верной категории; незаполненные "
            "атрибуты режут контент-рейтинг и релевантность поиска."
        ),
        "action": (
            "Сверить категорию с номенклатурой, заполнить вес/состав/"
            "назначение букета и ключевые фильтры покупателя."
        ),
        "source_label": "Яндекс Маркет — справка: карточки товаров / характеристики",
        "source_url": "https://yandex.ru/support/marketplace/",
    },
    "yandex_listing_basics": {
        "marketplace": "yandex",
        "topic": "placement",
        "title": "Размещение оффера",
        "rule": (
            "Для продаж на Маркете нужен активный оффер с ценой, остатком "
            "и привязанной карточкой (offer-mappings)."
        ),
        "action": (
            "Создать/обновить offer-mapping: цена, сток, штрихкод/артикул, "
            "привязка к карточке; убедиться что оффер не в архиве."
        ),
        "source_label": "Яндекс Маркет Partner API / справка: ассортимент",
        "source_url": "https://yandex.ru/dev/market/partner-api/",
    },
    "yandex_boost": {
        "marketplace": "yandex",
        "topic": "promotion",
        "title": "Продвижение и буст",
        "rule": (
            "После контента включают платное продвижение (буст / акции / "
            "ставки) в кабинете продавца — без ценовой конкурентоспособности "
            "и стока буст сжигает бюджет."
        ),
        "action": (
            "Сначала довести рейтинг и фото; затем проверить цену vs рынок "
            "и включить буст/акцию только на карточки со стоком > 0."
        ),
        "source_label": "Яндекс Маркет — справка: продвижение / буст / акции",
        "source_url": "https://yandex.ru/support/marketplace/",
    },
    "flowwow_listing_basics": {
        "marketplace": "flowwow",
        "topic": "placement",
        "title": "Карточка на Flowwow",
        "rule": (
            "Продажи идут с активных products: фото, цена, остаток, "
            "категория; скрытые/архивные не попадают в выдачу."
        ),
        "action": (
            "Создать product через API/кабинет: 4+ фото, актуальная цена, "
            "остаток, корректная категория; статус — активна."
        ),
        "source_label": "Flowwow — кабинет продавца / API products",
        "source_url": "https://flowwow.com/",
    },
    "flowwow_photos": {
        "marketplace": "flowwow",
        "topic": "content",
        "title": "Фото на витрине Flowwow",
        "rule": (
            "На цветочных витринах конверсия сильно зависит от числа и "
            "качества фото (общий план + детали + упаковка)."
        ),
        "action": (
            "Добавить минимум 3–5 фото: общий вид, крупный план цветов, "
            "упаковка/лента; убрать размытые и дубли."
        ),
        "source_label": "Flowwow — практика витрины / требования к фото",
        "source_url": "https://flowwow.com/",
    },
    "flowwow_showcase": {
        "marketplace": "flowwow",
        "topic": "promotion",
        "title": "Топ витрины Flowwow",
        "rule": (
            "Топ-позиции витрины (до ~18 слотов) дают заметно больше "
            "переходов; туда ставят сильные по фото/марже позиции."
        ),
        "action": (
            "После выкладки карточки поставить её в топ витрины, если "
            "маржа и сток позволяют; ротировать слабые позиции."
        ),
        "source_label": "Flowwow — витрина продавца (топ позиций)",
        "source_url": "https://flowwow.com/",
    },
    "flowwow_visibility": {
        "marketplace": "flowwow",
        "topic": "placement",
        "title": "Скрытые карточки",
        "rule": (
            "Скрытый product с готовым контентом не продаёт — его нужно "
            "открыть при наличии остатка, либо архивировать если снят."
        ),
        "action": (
            "Проверить остаток; если > 0 — открыть карточку; если 0 — "
            "пополнить или оставить скрытой осознанно."
        ),
        "source_label": "Flowwow — статусы products (active/hidden/archive)",
        "source_url": "https://flowwow.com/",
    },
    "general_photos": {
        "marketplace": "general",
        "topic": "content",
        "title": "Фотоконтент маркетплейса",
        "rule": (
            "Площадки и общие гайды сходятся: < 3 фото — слабая конверсия; "
            "оптимум 5–6 разноплановых кадров."
        ),
        "action": "Добавить фото до 5–6 шт. (ракурсы + контекст использования).",
        "source_label": "Общая практика маркетплейсов (фотоконтент)",
        "source_url": "",
    },
    "general_cross_market": {
        "marketplace": "general",
        "topic": "placement",
        "title": "Кросс-площадочное размещение",
        "rule": (
            "Сильная карточка на одной площадке — кандидат на вторую: "
            "тот же SKU, выровненные цена/фото/название."
        ),
        "action": (
            "Скопировать контент на вторую площадку, выровнять цену (±10%) "
            "и артикул, проверить сток на обоих каналах."
        ),
        "source_label": "Общая практика: мультиплощадочный ассортимент",
        "source_url": "",
    },
    "general_price_parity": {
        "marketplace": "general",
        "topic": "promotion",
        "title": "Паритет цен",
        "rule": (
            "Большой разрыв цен на один SKU между площадками ломает "
            "доверие и съедает маржу на «дешёвом» канале."
        ),
        "action": (
            "Выровнять цены или явно зафиксировать причину разницы "
            "(комплектация/доставка); иначе унифицировать."
        ),
        "source_label": "Общая практика ценообразования на маркетплейсах",
        "source_url": "",
    },
    "general_sku_hygiene": {
        "marketplace": "general",
        "topic": "placement",
        "title": "Один артикул — одна карточка",
        "rule": (
            "Дубли одного артикула дробят отзывы, рейтинг и остатки; "
            "поиск и реклама работают хуже."
        ),
        "action": (
            "Оставить одну каноническую карточку, остальные скрыть/"
            "объединить, перенести сток и отзывы куда возможно."
        ),
        "source_label": "Общая практика: гигиена SKU / дубли",
        "source_url": "",
    },
    "general_stock": {
        "marketplace": "general",
        "topic": "placement",
        "title": "Остатки перед показом",
        "rule": "Открывать скрытую карточку имеет смысл только при ненулевом стоке.",
        "action": "Сверить остаток в МойСклад / кабинете; пополнить или не открывать.",
        "source_label": "Общая практика: сток ↔ видимость",
        "source_url": "",
    },
}


def kb_entries_for_block(block_key: str) -> list[dict[str, str]]:
    """Return curated KB entries that ground a recommendation block."""
    ids = _BLOCK_KB.get(str(block_key or ""), ())
    out: list[dict[str, str]] = []
    for eid in ids:
        entry = ENTRIES.get(eid)
        if not entry:
            continue
        out.append({"id": eid, **entry})
    return out


def primary_action_for_block(block_key: str) -> str:
    """Concrete operator action from the first KB entry for the block."""
    entries = kb_entries_for_block(block_key)
    if not entries:
        return ""
    return str(entries[0].get("action") or "").strip()


def block_docs_meta(block_key: str) -> dict[str, str]:
    """Compact docs citation for UI meta (rule + source from KB)."""
    entries = kb_entries_for_block(block_key)
    if not entries:
        return {"docs": "", "docs_source": ""}
    first = entries[0]
    return {
        "docs": str(first.get("rule") or ""),
        "docs_source": str(first.get("source_label") or ""),
        "docs_action": str(first.get("action") or ""),
    }


def knowledge_payload() -> dict[str, Any]:
    """Full KB snapshot for /cards/recommendations (UI + tests)."""
    by_block = {
        key: kb_entries_for_block(key) for key in sorted(_BLOCK_KB)
    }
    return {
        "blocks": by_block,
        "entry_count": len(ENTRIES),
        "marketplaces": sorted(
            {e["marketplace"] for e in ENTRIES.values() if e.get("marketplace")}
        ),
    }


def format_kb_context(*, block_keys: list[str] | None = None, cap: int = 12) -> str:
    """Text block for the cards advisor prompt (retrieval-first grounding)."""
    keys = block_keys or list(_BLOCK_KB)
    seen: set[str] = set()
    lines: list[str] = [
        "База знаний площадок (инструкции по размещению/продвижению — опирайся на это):"
    ]
    count = 0
    for key in keys:
        for entry in kb_entries_for_block(key):
            eid = entry["id"]
            if eid in seen:
                continue
            seen.add(eid)
            src = entry.get("source_label") or "KB"
            lines.append(
                f"- [{entry.get('marketplace')}/{entry.get('topic')}] "
                f"{entry.get('title')}: {entry.get('rule')} "
                f"→ {entry.get('action')} (источник: {src})"
            )
            count += 1
            if count >= cap:
                return "\n".join(lines)
    return "\n".join(lines)

