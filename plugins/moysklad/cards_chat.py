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


def cards_chat_reply(
    messages: list[dict[str, str]],
    *,
    month_report: dict[str, Any],
    cards_summary: dict[str, Any] | None = None,
    provider: str = "",
    model: str = "",
) -> dict[str, Any]:
    """One chat turn. Returns ``{ok, reply}``."""
    prompt_messages: list[dict[str, str]] = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": build_context_prompt(month_report, cards_summary)},
        {
            "role": "assistant",
            "content": "Принял данные. Задавайте вопросы по отчёту и карточкам.",
        },
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
    return {"ok": True, "reply": text}
