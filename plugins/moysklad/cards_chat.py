"""Чат-аналитик for the «Карточки» tab.

Implements the client's dialogue methodology (call 22.08.2026): the
operator pastes / references the report, asks to rebuild held-out
months; the assistant must answer strictly from the provided MoySklad
figures, and when something is not derivable it must SAY which
marketplace / month / category figures it lacks instead of inventing
them. Manually supplied numbers in chat are then used for the rebuild.
"""

from __future__ import annotations

import json
from typing import Any

CARDS_CHAT_MAX_TOKENS = 1200
CARDS_CHAT_TIMEOUT = 60.0
_NO_REASONING = {"enabled": False, "effort": "none"}
_EXTRA_BODY = {"reasoning": {"enabled": False}}

_SYSTEM = """Ты — аналитик отчётности цветочной студии «Вереск» (МойСклад + маркетплейсы).

Данные ниже — ЕДИНСТВЕННЫЙ источник цифр: помесячный отчёт, рассчитанный из
оплаченных заказов МоегоСклада, плюс сводка карточек маркетплейсов.

Формулы отчёта (не переизобретай):
- прирост = (new/old) - 1
- выручка = оборот × (1 − комиссия площадки); комиссии: yandex_market 30%,
  flowwow/floday/skyloft 34.6%, flavy 30%, direct 0%
- средний чек = оборот / заказы; доля = оборот канала / общий оборот
- маржа = выручка − закупка × доля

Правила:
1. Считай только из данных ниже и цифр, которые оператор дал в чате.
2. Если для запрошенного месяца/канала/категории данных нет или их
   недостаточно — прямо скажи: «мне не хватает цифр с <площадка> за
   <месяц/категория>» и перечисли, что именно нужно. НИКОГДА не выдумывай
   недостающие числа.
3. Если оператор присылает недостающие цифры — пересчитай отчёт с ними.
4. Если оператор указывает на расхождение с образцом — разбери по формулам,
   откуда оно могло возникнуть (комиссия, неоплаченные заказы, канал
   классифицирован иначе, неполный месяц).
5. Отвечай по-русски, кратко, цифры — в явных таблицах или списках.
"""


def _compact_report(month_report: dict[str, Any], *, keep_months: int = 10) -> dict[str, Any]:
    months = sorted(month_report)[-keep_months:]
    out: dict[str, Any] = {}
    for month_id in months:
        out[month_id] = {
            ch: {k: v for k, v in (cell or {}).items() if v is not None}
            for ch, cell in sorted((month_report.get(month_id) or {}).items())
        }
    return out


def build_context_prompt(
    month_report: dict[str, Any],
    cards_summary: dict[str, Any] | None = None,
) -> str:
    parts = [
        "Помесячный отчёт из МоегоСклада (месяц → канал → метрики):",
        json.dumps(_compact_report(month_report), ensure_ascii=False),
    ]
    if cards_summary:
        parts += [
            "\nСводка карточек маркетплейсов:",
            json.dumps(cards_summary, ensure_ascii=False),
        ]
    parts.append(
        "\nЕсли в отчёте нет месяца или канала — этих данных в МоемСкладе нет."
    )
    return "\n".join(parts)


_CARDS_ADVISOR_SYSTEM = """Ты — консультант по карточкам маркетплейсов цветочной студии «Вереск»
(Flowwow, Яндекс Маркет). Ниже — реальные данные из API обоих кабинетов и
МоегоСклада: карточки (название, цена, статус, число фото, контент-рейтинг
Яндекса 0–100, на каких площадках размещена), помесячная динамика продаж по
каналам из МоегоСклада и реальные продажи из кабинета Яндекса.

Что система УМЕЕТ через подключённые API (учитывай это в рекомендациях):
- Flowwow (открытое API продавца): читать/создавать/изменять карточки
  (products/create, products/update), скрывать/открывать/архивировать,
  менять цены и остатки, топ-18 витрины. Заказов и позиций выдачи в
  открытом API НЕТ.
- Яндекс Маркет (партнёрское API): читать/изменять карточки
  (offer-mappings), контент-рейтинг каждой карточки, цены и остатки,
  читать реальные заказы и статистику продаж (stats/orders).
- МойСклад: каталог букетов с составом, заказы по всем каналам.

Твоя зона: размещение, продвижение, исправление и добавление карточек.

ФОРМАТ ОТВЕТА — ЖЁСТКО:
- Никаких заголовков #, ##, «Анализ», «Приоритет действий», вступлений и
  выводов. Сразу пункты.
- Каждый пункт: «Название карточки» — факт из данных (рейтинг/фото/цена/
  площадка) → конкретное действие. Один пункт = одна строка-две.
- Максимум 10 пунктов на ответ. Отвечай ровно на заданный вопрос, не
  вываливай всё, что знаешь.
- Каждое утверждение обязано опираться на строку из данных ниже. Нет данных
  («проверьте», «возможно», «убедитесь») — не пиши пункт вовсе; вместо
  догадок одной строкой скажи, каких данных не хватает.
- Рейтинг ниже ~85 на Яндексе — первый приоритет; карточка только на одной
  площадке — кандидат на вторую с учётом динамики канала.
"""


def build_cards_context(combined: list[dict[str, Any]], *, cap: int = 600) -> str:
    """Compact per-card lines for the advisor prompt."""
    lines: list[str] = []
    for row in (combined or [])[:cap]:
        listings = row.get("listings") or {}
        bits: list[str] = []
        for mp, product in sorted(listings.items()):
            rating = product.get("content_rating")
            bits.append(
                f"{mp}: {_card_status_ru(product)}"
                + (f", фото {product.get('images_count')}" if product.get("images_count") is not None else "")
                + (f", цена {product.get('price')}" if product.get("price") else "")
                + (f", рейтинг {rating}/100" if rating is not None else "")
            )
        lines.append(f"- «{row.get('name') or '—'}» [{' | '.join(bits)}]")
    return "\n".join(lines)


def _card_status_ru(product: dict[str, Any]) -> str:
    if product.get("is_archived"):
        return "архив"
    return "активна" if product.get("is_active") else "скрыта"


def cards_advisor_reply(
    messages: list[dict[str, str]],
    *,
    combined: list[dict[str, Any]],
    channel_dynamics: dict[str, Any] | None = None,
    yandex_stats: dict[str, Any] | None = None,
    provider: str = "",
    model: str = "",
) -> dict[str, Any]:
    """One chat turn of the placement/promotion advisor. ``{ok, reply}``.

    Retrieval-first: the model gets the precomputed recommendation blocks
    plus only the cards matching the question (token search), not the whole
    catalog — answers stay grounded and short.
    """
    from plugins.moysklad.cards_recommendations import (
        build_recommendations,
        retrieve_cards,
    )

    query = ""
    for turn in reversed(messages or []):
        if str((turn or {}).get("role") or "") == "user":
            query = str(turn.get("content") or "")
            break
    recommendations = build_recommendations(combined)
    retrieved = retrieve_cards(combined, query, k=40)

    parts = [
        "Готовые рекомендации, посчитанные из данных (это факты, не догадки):",
        json.dumps(recommendations, ensure_ascii=False),
    ]
    if retrieved:
        parts += [
            "\nКарточки, найденные по вопросу:",
            build_cards_context(retrieved, cap=40) or "(пусто)",
        ]
    if channel_dynamics:
        parts += [
            "\nПродажи по каналам из МоегоСклада (месяц → канал → [оборот, заказы]):",
            json.dumps(channel_dynamics, ensure_ascii=False),
        ]
    if yandex_stats:
        parts += [
            "\nРеальные продажи из кабинета Яндекс Маркета (после скидок):",
            json.dumps(yandex_stats, ensure_ascii=False),
        ]
    context = "\n".join(parts)
    return _chat_turn(
        messages,
        system=_CARDS_ADVISOR_SYSTEM,
        context=context,
        ack="Карточки загружены. Спрашивайте про размещение и продвижение.",
        provider=provider,
        model=model,
    )


def cards_chat_reply(
    messages: list[dict[str, str]],
    *,
    month_report: dict[str, Any],
    cards_summary: dict[str, Any] | None = None,
    provider: str = "",
    model: str = "",
) -> dict[str, Any]:
    """One chat turn of the report analyst (Дашборд). Returns ``{ok, reply}``."""
    return _chat_turn(
        messages,
        system=_SYSTEM,
        context=build_context_prompt(month_report, cards_summary),
        ack="Принял данные. Задавайте вопросы по отчёту.",
        provider=provider,
        model=model,
    )


_FOLLOWUP_MARKER = "[ДАЛЬШЕ]"

_FOLLOWUP_RULES = f"""

В самом конце ответа добавь блок:
{_FOLLOWUP_MARKER}
и ровно 3 коротких (до 80 знаков) follow-up вопроса, каждый с новой строки —
что оператор логично спросит ДАЛЬШЕ, исходя из твоего ответа (уточнение,
следующий шаг, соседняя проблема из данных). Без нумерации и маркеров.
"""


def split_followups(text: str) -> tuple[str, list[str]]:
    """Reply text → (clean reply, ≤3 follow-up questions)."""
    if _FOLLOWUP_MARKER not in text:
        return text.strip(), []
    reply, _, tail = text.partition(_FOLLOWUP_MARKER)
    followups: list[str] = []
    for line in tail.splitlines():
        line = line.strip().lstrip("-–—•*0123456789. ").strip()
        if line:
            followups.append(line[:120])
        if len(followups) == 3:
            break
    return reply.strip(), followups


def _chat_turn(
    messages: list[dict[str, str]],
    *,
    system: str,
    context: str,
    ack: str,
    provider: str = "",
    model: str = "",
) -> dict[str, Any]:
    prompt_messages: list[dict[str, str]] = [
        {"role": "system", "content": system + _FOLLOWUP_RULES},
        {"role": "user", "content": context},
        {"role": "assistant", "content": ack},
    ]
    for turn in (messages or [])[-16:]:
        role = str((turn or {}).get("role") or "").strip().lower()
        content = str((turn or {}).get("content") or "").strip()
        if content and role in ("user", "assistant"):
            prompt_messages.append({"role": role, "content": content[:6000]})

    from agent.auxiliary_client import call_llm, extract_content_or_reasoning

    kwargs: dict[str, Any] = {
        "task": "moysklad_outreach",
        "messages": prompt_messages,
        "max_tokens": CARDS_CHAT_MAX_TOKENS,
        "temperature": 0.3,
        "timeout": CARDS_CHAT_TIMEOUT,
        "reasoning_config": _NO_REASONING,
        "extra_body": _EXTRA_BODY,
    }
    if (provider or "").strip():
        kwargs["provider"] = provider.strip()
    if (model or "").strip():
        kwargs["model"] = model.strip()
    response = call_llm(**kwargs)
    text = (extract_content_or_reasoning(response) or "").strip()
    if not text:
        return {"ok": False, "error": "empty_llm_response", "reply": ""}
    reply, followups = split_followups(text)
    return {"ok": True, "reply": reply, "followups": followups}
