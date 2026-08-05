"""AI outreach draft text for MoySklad campaigns (Telegram / WhatsApp).

Grounded on the same client-card facts + recommendation guardrails.
Never invents discounts, orders, phones, or VIP status.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from plugins.moysklad.client_card import (
    _facts_payload,
    build_client_detail,
    generate_ai_for_detail,
)

log = logging.getLogger(__name__)

_OUTREACH_SYSTEM = """Ты — копирайтер цветочного магазина Iris. Пишешь исходящие сообщения
клиенту в WhatsApp или Telegram. Отвечай строго на русском.

ЖЁСТКИЕ ПРАВИЛА:
1. Используй ТОЛЬКО факты из JSON (client/orders/ai). Ничего не выдумывай.
2. Запрещено придумывать скидки, промокоды, акции, телефоны, заказы, даты,
   суммы, VIP, баллы, адреса — если нет в JSON, не упоминай.
3. Не обещай наличие конкретных букетов/SKU, которых нет в сниппетах заказов.
4. Тон: тёплый, короткий, продающий без давления. 2–6 предложений.
5. Обращение по имени, если имя есть. Без канцелярита и markdown.
6. Канал сообщения указан в user prompt — адаптируй формат (личные сообщения).
7. Если data_thin=true или заказов мало — мягкий контакт без ссылок на «историю».
8. Ответ — строго JSON без markdown:
{"message": "текст сообщения клиенту", "grounding_notes": "1-2 предложения: на каких фактах основан текст"}
"""


def facts_panel(detail: dict[str, Any]) -> dict[str, Any]:
    """Compact grounded facts for the seller audit side panel."""
    client = detail.get("client") or {}
    stats = detail.get("stats") or {}
    messaging = detail.get("messaging") or {}
    ai = detail.get("ai") or {}
    orders = list(detail.get("orders") or [])
    last = stats.get("last_order") or (orders[0] if orders else None)
    buckets = client.get("tag_buckets") or {}
    return {
        "client_id": client.get("id"),
        "name": client.get("name"),
        "phone": client.get("phone") or None,
        "email": client.get("email") or None,
        "tg_nick": client.get("tg_nick") or None,
        "sales_type": client.get("sales_type") or None,
        "channels": list(client.get("channels") or []),
        "primary_channel": client.get("primary_channel")
        or messaging.get("primary_channel")
        or None,
        "order_count": int(stats.get("order_count") or client.get("order_count") or 0),
        "avg_check": float(stats.get("avg_check") or client.get("avg_check") or 0),
        "vip": bool(stats.get("vip") or client.get("vip")),
        "loyalty_points": stats.get("loyalty_points", client.get("loyalty_points")),
        "last_order": last,
        "tags": list(client.get("tags") or []),
        "event_tags": list(buckets.get("events") or []),
        "orders_preview": [
            {
                "date": (o.get("date") or "")[:16],
                "sum": o.get("sum"),
                "channel": o.get("channel") or None,
                "product_snippet": o.get("product_snippet") or None,
            }
            for o in orders[:8]
        ],
        "data_thin": bool(detail.get("data_thin") or ai.get("data_thin")),
        "recommendation": (ai.get("recommendation") or "").strip() or None,
        "history_profile": (ai.get("history_profile") or "").strip() or None,
        "occasion_intent": (ai.get("occasion_intent") or "").strip() or None,
        "ai_source": ai.get("source") or None,
    }


def _channel_label(channel: str) -> str:
    ch = (channel or "telegram").strip().lower()
    if ch == "whatsapp":
        return "WhatsApp"
    if ch == "telegram_channel":
        return "Telegram-канал"
    return "Telegram (личные)"


def heuristic_outreach_message(
    detail: dict[str, Any],
    *,
    channel: str = "telegram",
) -> dict[str, Any]:
    """Deterministic draft from facts + recommendation (no LLM)."""
    client = detail.get("client") or {}
    stats = detail.get("stats") or {}
    ai = detail.get("ai") or {}
    orders = list(detail.get("orders") or [])
    name = str(client.get("name") or "").strip() or "клиент"
    first = name.split()[0] if name else "клиент"
    avg = float(stats.get("avg_check") or client.get("avg_check") or 0)
    last = stats.get("last_order") or (orders[0] if orders else None)
    data_thin = bool(detail.get("data_thin") or ai.get("data_thin"))
    ch_label = _channel_label(channel)

    greeting = f"Здравствуйте, {first}!"
    if data_thin or not orders:
        message = (
            f"{greeting} Это Iris, цветочный магазин. "
            f"Хотели мягко напомнить о себе — если планируете букет или доставку, "
            f"напишите, подберём вариант под ваш повод. "
            f"(канал: {ch_label})"
        )
        notes = "Мало фактов в карточке — без ссылок на заказы и скидки."
    else:
        last_bit = ""
        if last:
            d = str(last.get("date") or "")[:10]
            snip = str(last.get("product_snippet") or "").strip()
            if d:
                last_bit = f" Последний заказ у нас был {d}"
                if snip:
                    last_bit += f" ({snip[:60]})"
                last_bit += "."
        check_bit = f" Ориентир по прошлым заказам ≈ {avg:.0f} ₽." if avg else ""
        occasion = ""
        intent = str(ai.get("occasion_intent") or "")
        if "март" in intent.lower() or "8" in intent:
            occasion = " Если снова ждёте весенний повод — можем заранее подготовить букет."
        elif "валентин" in intent.lower() or "феврал" in intent.lower():
            occasion = " Если близок февральский повод — напишите, забронируем состав."
        elif "сентябр" in intent.lower() or "1 сентября" in intent.lower():
            occasion = " Если актуален сентябрьский повод — подскажем варианты заранее."
        message = (
            f"{greeting} Это Iris.{last_bit}{check_bit}{occasion} "
            f"Напишите, если удобно продолжить подбор — без навязанных скидок, "
            f"только по вашей истории. ({ch_label})"
        )
        notes = "Опирается на даты/суммы/сниппеты из кэша заказов; скидки не выдуманы."

    return {
        "message": message.strip(),
        "grounding_notes": notes,
        "source": "heuristic",
        "channel": (channel or "telegram").strip().lower(),
        "facts": facts_panel(detail),
    }


def _parse_outreach_json(text: str) -> Optional[dict[str, str]]:
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        lines = raw.splitlines()
        if len(lines) >= 2 and lines[0].startswith("```"):
            if lines[-1].strip() == "```":
                raw = "\n".join(lines[1:-1]).strip()
            else:
                raw = "\n".join(lines[1:]).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    message = str(data.get("message") or "").strip()
    if not message:
        return None
    return {
        "message": message,
        "grounding_notes": str(data.get("grounding_notes") or "").strip(),
    }


def generate_outreach_message(
    detail: dict[str, Any],
    *,
    channel: str = "telegram",
    refresh_ai: bool = False,
) -> dict[str, Any]:
    """Generate editable outreach text; fall back to heuristic on LLM failure."""
    channel = (channel or "telegram").strip().lower()
    if refresh_ai or not (detail.get("ai") or {}).get("recommendation"):
        detail = {**detail, "ai": generate_ai_for_detail(detail)}

    fallback = heuristic_outreach_message(detail, channel=channel)
    facts = _facts_payload(detail)
    payload = {
        "channel": channel,
        "channel_label": _channel_label(channel),
        "client": facts.get("client"),
        "orders": facts.get("orders"),
        "data_thin": facts.get("data_thin"),
        "ai": {
            "history_profile": (detail.get("ai") or {}).get("history_profile"),
            "occasion_intent": (detail.get("ai") or {}).get("occasion_intent"),
            "recommendation": (detail.get("ai") or {}).get("recommendation"),
            "source": (detail.get("ai") or {}).get("source"),
            "data_thin": (detail.get("ai") or {}).get("data_thin"),
        },
    }
    user = (
        "Сгенерируй текст личного сообщения клиенту.\n"
        f"Канал отправки: {_channel_label(channel)}.\n"
        "JSON фактов (единственный источник истины):\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
    try:
        from agent.auxiliary_client import call_llm, extract_content_or_reasoning

        response = call_llm(
            task="compression",
            messages=[
                {"role": "system", "content": _OUTREACH_SYSTEM},
                {"role": "user", "content": user},
            ],
            max_tokens=700,
            temperature=0.25,
            timeout=45.0,
        )
        text = (extract_content_or_reasoning(response) or "").strip()
        parsed = _parse_outreach_json(text)
        if not parsed:
            log.warning("moysklad outreach AI: empty/unparsed, using heuristic")
            return fallback
        return {
            "message": parsed["message"],
            "grounding_notes": parsed.get("grounding_notes")
            or fallback["grounding_notes"],
            "source": "llm",
            "channel": channel,
            "facts": facts_panel(detail),
            "ai": detail.get("ai"),
        }
    except Exception as exc:
        log.warning("moysklad outreach AI unavailable: %s", exc)
        return {**fallback, "error": str(exc), "ai": detail.get("ai")}


def build_outreach_for_row(
    row: dict[str, Any],
    *,
    channel: str = "telegram",
    refresh_ai: bool = True,
) -> dict[str, Any]:
    """Catalog row → detail → outreach draft payload."""
    detail = build_client_detail(row)
    result = generate_outreach_message(
        detail, channel=channel, refresh_ai=refresh_ai
    )
    result["detail_ok"] = True
    result["client_id"] = (detail.get("client") or {}).get("id")
    result["client_name"] = (detail.get("client") or {}).get("name")
    return result
