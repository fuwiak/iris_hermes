"""Marketplace Ranking Engine — per-marketplace rulebooks with official weights.

Codifies the published ranking factors of both marketplaces so the СЕО tab
produces «данные карточки → правило площадки → проблема → действие →
ожидаемый эффект → приоритет» instead of generic AI advice.

Sources:
- Flowwow, «Выдача товаров: как подняться в топе»
  https://flowwow.com/blog/vydacha-tovarov-kak-podnyatsya-v-tope/
  (behavioral loop: фото → CTR → просмотры → конверсия → заказы → позиция;
  keywords в названии/описании; рейтинг магазина; повторные покупки; Top-18)
- Яндекс Маркет, «Качество карточки» + «Товары в поиске»
  https://yandex.ru/support/marketplace/ru/assortment/content/quality
  (official weights: релевантность 27,5%, интересы пользователя 25,5%,
  цена 18%, популярность 17%, рейтинг/отзывы 7,5%, доставка 4,5%;
  качество карточки до 100 баллов — карточки ≥80 получают до +50% показов)
"""

from __future__ import annotations

import re
from typing import Any

FLOWWOW_BLOG_URL = "https://flowwow.com/blog/vydacha-tovarov-kak-podnyatsya-v-tope/"
YANDEX_QUALITY_URL = "https://yandex.ru/support/marketplace/ru/assortment/content/quality"

_DIGIT_RE = re.compile(r"\d")

# ---------------------------------------------------------------------------
# Rulebook — one entry per published ranking factor. `checkable` marks the
# factors we can audit from card data today; the rest stay as reference rows
# so the operator sees the WHOLE model, not only what we measure.
# ---------------------------------------------------------------------------

RANKING_RULEBOOK: list[dict[str, str | bool]] = [
    # Flowwow — behavioral loop
    {
        "id": "fw_photo_ctr",
        "marketplace": "flowwow",
        "factor": "Главное фото → CTR карточки",
        "weight": "петля ранжирования: фото → CTR → просмотры → конверсия → позиция",
        "detail": (
            "Flowwow прямо пишет: привлекательное главное фото даёт больше "
            "переходов из выдачи, переходы дают заказы, заказы поднимают позицию."
        ),
        "source": FLOWWOW_BLOG_URL,
        "checkable": True,
    },
    {
        "id": "fw_title_keywords",
        "marketplace": "flowwow",
        "factor": "Название + ключевые слова",
        "weight": "поисковая выдача по запросу",
        "detail": (
            "Ключевые слова в названии и описании повышают шанс показа по "
            "запросу; название не должно быть переспамлено фразами."
        ),
        "source": FLOWWOW_BLOG_URL,
        "checkable": True,
    },
    {
        "id": "fw_conversion_content",
        "marketplace": "flowwow",
        "factor": "Описание / полнота контента → конверсия",
        "weight": "конверсия просмотр → заказ — один из ключевых факторов",
        "detail": "Полное описание и контент влияют на решение и конверсию в заказ.",
        "source": FLOWWOW_BLOG_URL,
        "checkable": True,
    },
    {
        "id": "fw_visibility",
        "marketplace": "flowwow",
        "factor": "Видимость карточки (скрыта/активна)",
        "weight": "скрытая карточка = 0 показов",
        "detail": "Скрытые и архивные карточки не участвуют в выдаче вовсе.",
        "source": FLOWWOW_BLOG_URL,
        "checkable": True,
    },
    {
        "id": "fw_shop_rating",
        "marketplace": "flowwow",
        "factor": "Рейтинг магазина + повторные покупки",
        "weight": "прямо влияет на органическую видимость",
        "detail": (
            "Рейтинг магазина, доля повторных покупок, отмены и сервис "
            "(Супермагазин) поднимают все карточки магазина."
        ),
        "source": FLOWWOW_BLOG_URL,
        "checkable": False,
    },
    {
        "id": "fw_top18",
        "marketplace": "flowwow",
        "factor": "Top-18 витрины / WowPass",
        "weight": "инструменты продвижения",
        "detail": "Flowwow рекомендует держать лучшие карточки в Top-18 витрины.",
        "source": FLOWWOW_BLOG_URL,
        "checkable": False,
    },
    # Yandex Market — official weights
    {
        "id": "ym_relevance",
        "marketplace": "yandex_market",
        "factor": "Соответствие товара запросу",
        "weight": "27,5%",
        "detail": (
            "Название + описание + характеристики против запроса. «Букет из "
            "25 красных роз 50 см» бьёт «Букет роз» по запросу «25 красных роз». "
            "Незаполненный параметр-фильтр выкидывает товар из фильтрованной выдачи."
        ),
        "source": YANDEX_QUALITY_URL,
        "checkable": True,
    },
    {
        "id": "ym_affinity",
        "marketplace": "yandex_market",
        "factor": "Соответствие интересам пользователя",
        "weight": "25,5%",
        "detail": (
            "Персональный recommender (поиски/покупки/регион). Продавец напрямую "
            "не оптимизирует — влияет косвенно через популярность и качество."
        ),
        "source": YANDEX_QUALITY_URL,
        "checkable": False,
    },
    {
        "id": "ym_price",
        "marketplace": "yandex_market",
        "factor": "Цена",
        "weight": "18%",
        "detail": (
            "Чем ближе к конкурентной/минимальной на рынке, тем выше. Маркет "
            "сравнивает конкурентность цены и предлагает рекомендованную."
        ),
        "source": YANDEX_QUALITY_URL,
        "checkable": True,
    },
    {
        "id": "ym_popularity",
        "marketplace": "yandex_market",
        "factor": "Популярность товара",
        "weight": "17%",
        "detail": "Заказы, корзины, избранное, открытия карточки, просмотры.",
        "source": YANDEX_QUALITY_URL,
        "checkable": False,
    },
    {
        "id": "ym_rating_reviews",
        "marketplace": "yandex_market",
        "factor": "Оценка + количество отзывов",
        "weight": "7,5%",
        "detail": "Рейтинг товара и число отзывов напрямую входят в формулу выдачи.",
        "source": YANDEX_QUALITY_URL,
        "checkable": False,
    },
    {
        "id": "ym_delivery",
        "marketplace": "yandex_market",
        "factor": "Срок доставки",
        "weight": "4,5%",
        "detail": "Короче срок — выше score: частые отгрузки FBS, same-day, Express.",
        "source": YANDEX_QUALITY_URL,
        "checkable": False,
    },
    {
        "id": "ym_card_quality",
        "marketplace": "yandex_market",
        "factor": "Качество карточки (до 100 баллов)",
        "weight": "~14,5% в альтернативной декомпозиции; ≥80 баллов → до +50% показов",
        "detail": (
            "Полное название + описание + характеристики + фото + видео/"
            "инфографика максимизируют Quality Score."
        ),
        "source": YANDEX_QUALITY_URL,
        "checkable": True,
    },
]


def _price_of(product: dict[str, Any]) -> float:
    try:
        return float(product.get("price") or 0)
    except (TypeError, ValueError):
        return 0.0


def _finding(
    rule_id: str,
    priority: str,
    problem: str,
    action: str,
    expected: str,
) -> dict[str, str]:
    rule = next(r for r in RANKING_RULEBOOK if r["id"] == rule_id)
    return {
        "rule_id": rule_id,
        "marketplace": str(rule["marketplace"]),
        "factor": str(rule["factor"]),
        "weight": str(rule["weight"]),
        "priority": priority,
        "problem": problem,
        "action": action,
        "expected": expected,
        "source": str(rule["source"]),
    }


def evaluate_card(
    card: dict[str, Any],
    *,
    min_photos_fw: int = 4,
    min_photos_ym: int = 5,
    quality_threshold: int = 80,
    min_title_len: int = 25,
    min_desc_len: int = 300,
    price_gap_min: float = 0.10,
) -> list[dict[str, str]]:
    """Audit one combined card against the rulebook. Returns findings."""
    findings: list[dict[str, str]] = []
    listings: dict[str, dict[str, Any]] = card.get("listings") or {}
    name = str(card.get("name") or "")
    fw = listings.get("flowwow")
    ym = listings.get("yandex_market")

    def _desc(product: dict[str, Any] | None) -> str:
        if not product:
            return ""
        return str(product.get("description") or product.get("description_preview") or "")

    # --- Flowwow ---
    if fw and not fw.get("is_archived"):
        photos = int(fw.get("images_count") or 0)
        if not fw.get("is_active"):
            findings.append(
                _finding(
                    "fw_visibility",
                    "high",
                    "Карточка скрыта — Flowwow не показывает её в выдаче вообще.",
                    "Проверить остатки и открыть карточку (products/unhide).",
                    "Карточка возвращается в выдачу → появляются показы и CTR-сигналы.",
                )
            )
        if photos < min_photos_fw:
            findings.append(
                _finding(
                    "fw_photo_ctr",
                    "high",
                    f"Всего {photos} фото (порог {min_photos_fw}). Главное фото — вход в петлю CTR → просмотры → заказы.",
                    "Добавить крупный план товара без лишнего фона + ракурсы и упаковку (4–6 фото).",
                    "Рост CTR из выдачи → больше просмотров → рост конверсии → позиция выше.",
                )
            )
        if len(name) < min_title_len or not _DIGIT_RE.search(name):
            gap = "без количества/размера" if not _DIGIT_RE.search(name) else f"короткое ({len(name)} симв.)"
            findings.append(
                _finding(
                    "fw_title_keywords",
                    "medium",
                    f"Название «{name[:60]}» {gap} — слабое совпадение с запросами покупателей.",
                    "Добавить в название состав и размер («25 красных роз, 50 см»), без переспама.",
                    "Больше показов по целевым запросам покупателя.",
                )
            )
        if len(_desc(fw)) < min_desc_len:
            findings.append(
                _finding(
                    "fw_conversion_content",
                    "medium",
                    f"Описание {len(_desc(fw))} символов (порог {min_desc_len}) — не хватает контента для решения о заказе.",
                    "Расписать состав, повод, уход и доставку (аквабокс, инструкция).",
                    "Выше конверсия просмотр → заказ — один из ключевых факторов Flowwow.",
                )
            )

    # --- Yandex Market ---
    if ym and not ym.get("is_archived"):
        photos = int(ym.get("images_count") or 0)
        rating = ym.get("content_rating")
        if rating is not None and int(rating) < quality_threshold:
            findings.append(
                _finding(
                    "ym_card_quality",
                    "high",
                    f"Контент-рейтинг {rating}/100 — ниже порога {quality_threshold}. Карточки ≥80 получают до +50% показов.",
                    "Заполнить характеристики (включая фильтруемые), добавить фото/видео, расширить описание.",
                    f"Рейтинг ≥{quality_threshold} → до +50% показов, заказов и продаж по данным Маркета.",
                )
            )
        if photos < min_photos_ym:
            findings.append(
                _finding(
                    "ym_card_quality",
                    "high" if photos < 3 else "medium",
                    f"{photos} фото (порог {min_photos_ym}) — фото входят в Quality Score карточки.",
                    "Добавить до 5–7 фото: основное, ракурсы, в интерьере; по возможности видео/инфографику.",
                    "Выше Quality Score → больше показов (фактор ~14,5%).",
                )
            )
        if len(name) < min_title_len or not _DIGIT_RE.search(name):
            gap = "без количества/размера" if not _DIGIT_RE.search(name) else f"короткое ({len(name)} симв.)"
            findings.append(
                _finding(
                    "ym_relevance",
                    "high",
                    f"Название «{name[:60]}» {gap} — слабая релевантность запросу (вес 27,5%).",
                    "Полное название по схеме «тип + состав + количество + размер»; заполнить характеристики-фильтры.",
                    "Рост релевантности — самый тяжёлый фактор выдачи (27,5%).",
                )
            )
        if len(_desc(ym)) < min_desc_len:
            findings.append(
                _finding(
                    "ym_relevance",
                    "medium",
                    f"Описание {len(_desc(ym))} символов — описание участвует в соответствии запросу (27,5%).",
                    "Расширить описание ключевыми словами покупателя (без спама) и деталями состава.",
                    "Лучшее совпадение с запросами → выше позиция в поиске Маркета.",
                )
            )
        fw_price = _price_of(fw) if fw else 0.0
        ym_price = _price_of(ym)
        if fw_price > 0 and ym_price > 0 and (ym_price - fw_price) / fw_price > price_gap_min:
            gap_pct = round((ym_price - fw_price) / fw_price * 100)
            findings.append(
                _finding(
                    "ym_price",
                    "medium",
                    f"Цена на Маркете {ym_price:,.0f} ₽ выше Flowwow ({fw_price:,.0f} ₽) на {gap_pct}%. Вес цены — 18%.",
                    f"Проверить конкурентность: снизить до ~{fw_price:,.0f}–{fw_price * 1.05:,.0f} ₽ или подтвердить разницу.",
                    "Ближе к конкурентной цене → выше ранжирование (18%) + доступ к бусту без сжигания бюджета.",
                )
            )

    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: order.get(f["priority"], 3))
    return findings


def seo_audit(
    combined: list[dict[str, Any]],
    *,
    cap: int = 40,
    **thresholds: Any,
) -> dict[str, Any]:
    """Rulebook + per-card findings for the СЕО tab, worst cards first."""
    audited: list[dict[str, Any]] = []
    for card in combined or []:
        findings = evaluate_card(card, **thresholds)
        if findings:
            audited.append(
                {
                    "name": card.get("name") or "",
                    "image": card.get("image") or "",
                    "marketplaces": list(card.get("marketplaces") or []),
                    "high": sum(1 for f in findings if f["priority"] == "high"),
                    "medium": sum(1 for f in findings if f["priority"] == "medium"),
                    "findings": findings,
                }
            )
    audited.sort(key=lambda c: (-c["high"], -c["medium"]))
    return {
        "rulebook": RANKING_RULEBOOK,
        "cards": audited[:cap],
        "cards_with_findings": len(audited),
        "cards_total": len(combined or []),
    }
