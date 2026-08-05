"""AI outreach draft text for MoySklad campaigns (Telegram / WhatsApp).

Grounded on the same client-card facts + recommendation guardrails.
Never invents discounts, orders, phones, or VIP status.
Seller identity comes from editable shop settings (not hardcoded Iris).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Optional

from plugins.moysklad.client_card import (
    _facts_payload,
    build_client_detail,
    generate_ai_for_detail,
)

log = logging.getLogger(__name__)

_MONTHS_RU = (
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)

# Internal order names / codes like "1605-02", "100", "З-12345"
_ORDER_CODE_RE = re.compile(
    r"^(?:з[-\s]?)?\d{1,6}(?:[-\s/]\d{1,6})?$",
    re.IGNORECASE,
)

_DEFAULT_SELLER_NAME = "цветочный магазин"


def _OUTREACH_SYSTEM(seller_name: str, seller_facts: str) -> str:
    sig = (seller_name or "").strip() or _DEFAULT_SELLER_NAME
    facts = (seller_facts or "").strip()
    facts_block = (
        f"Факты о продавце/магазине (можно мягко использовать, не выдумывай сверх них):\n{facts}\n"
        if facts
        else "Факты о продавце не заданы — не выдумывай адрес, акции, спецпредложения.\n"
    )
    return f"""Ты — копирайтер цветочного магазина. Пишешь исходящие сообщения
клиенту в WhatsApp или Telegram от имени продавца. Отвечай строго на русском.

ПОДПИСЬ / ИМЯ ПРОДАВЦА: {sig}
{facts_block}
ЖЁСТКИЕ ПРАВИЛА:
1. Используй ТОЛЬКО факты из JSON (client/orders/ai) + подпись/факты продавца выше.
   Ничего не выдумывай: скидки, промокоды, акции, телефоны, заказы, даты, суммы,
   VIP, баллы, адреса — если нет в данных, не упоминай.
2. Не обещай наличие конкретных букетов/SKU, которых нет в сниппетах заказов.
3. Тон: культурный, тёплый, продающий без давления. Как живой продавец в переписке
   цветочного магазина — не робот, не канцелярит, не markdown.
4. НЕ пиши мета-фразы вроде «без навязанных скидок», «только по вашей истории»,
   «без выдуманных скидок», «ориентир по прошлым заказам».
5. Даты — по-человечески (например «в середине мая»), не сырой ISO (2026-05-16).
   Не вставляй внутренние номера заказов/коды вроде «1605-02», если это не название букета.
6. Представься через подпись продавца (поле выше), НЕ хардкодь «Это Iris», если
   подпись другая. Если подпись пустая — мягко «из цветочного магазина».
7. Канал указан в user prompt — пиши под него. НЕ добавляй в конец текста
   ярлык канала вроде «(WhatsApp)» / «(Telegram)».
8. Обращение по имени, если есть. 2–5 коротких предложений. Можно мягко
   напомнить о поводе (8 марта, февраль, 1 сентября), если это есть в JSON.
9. Ответ — строго JSON без markdown:
{{"message": "текст сообщения клиенту", "grounding_notes": "1-2 предложения: на каких фактах основан текст"}}
"""


_REWRITE_SYSTEM = """Ты — редактор исходящих сообщений цветочного магазина.
Перепиши черновик так, чтобы он стал продающим и по-человечески (вежливо, тепло,
без роботского тона и без грубости). Отвечай строго на русском.

ЖЁСТКИЕ ПРАВИЛА:
1. Не выдумывай скидки, промокоды, акции, заказы, даты, суммы, VIP, адреса.
2. Сохраняй смысл и все реальные факты из исходного текста; можно смягчить формулировки.
3. Убери мета-фразы («без навязанных скидок», «только по истории»), сырые ISO-даты,
   внутренние коды заказов, ярлыки канала в скобках в конце.
4. Используй подпись/факты продавца из user prompt, если они есть.
5. Канал — как указано; не меняй канал и не дописывай «(WhatsApp)»/«(Telegram)».
6. 2–5 коротких предложений, живой тон переписки.
7. Ответ — строго JSON без markdown:
{"message": "новый текст", "grounding_notes": "что сохранили / что смягчили"}
"""


def normalize_seller_fields(
    seller_name: str | None = None,
    seller_facts: str | None = None,
) -> tuple[str, str]:
    name = str(seller_name or "").strip()
    facts = str(seller_facts or "").strip()
    return name, facts


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


def _format_human_date(raw: Any) -> str:
    """Turn ISO / MoySklad datetime into a short Russian phrase."""
    s = str(raw or "").strip()
    if not s:
        return ""
    head = s[:10]
    try:
        dt = datetime.strptime(head, "%Y-%m-%d")
    except ValueError:
        return ""
    month = _MONTHS_RU[dt.month] if 1 <= dt.month <= 12 else ""
    if not month:
        return ""
    if dt.day <= 10:
        return f"в начале {month}"
    if dt.day >= 21:
        return f"в конце {month}"
    return f"в середине {month}"


def _natural_product_bit(snippet: Any) -> str:
    snip = str(snippet or "").strip()
    if not snip:
        return ""
    if _ORDER_CODE_RE.match(snip):
        return ""
    # Mostly digits / codes
    if re.fullmatch(r"[\d\s\-_/]+", snip):
        return ""
    return snip[:60]


def _seller_intro(seller_name: str) -> str:
    name = (seller_name or "").strip()
    if not name:
        return "Пишу из цветочного магазина."
    low = name.lower()
    if low.startswith("это ") or " из " in low:
        return f"{name}." if not name.endswith((".", "!", "?")) else name
    return f"Это {name}."


def heuristic_outreach_message(
    detail: dict[str, Any],
    *,
    channel: str = "telegram",
    seller_name: str = "",
    seller_facts: str = "",
) -> dict[str, Any]:
    """Deterministic draft from facts + recommendation (no LLM)."""
    seller_name, seller_facts = normalize_seller_fields(seller_name, seller_facts)
    client = detail.get("client") or {}
    stats = detail.get("stats") or {}
    ai = detail.get("ai") or {}
    orders = list(detail.get("orders") or [])
    name = str(client.get("name") or "").strip() or "клиент"
    first = name.split()[0] if name else "клиент"
    avg = float(stats.get("avg_check") or client.get("avg_check") or 0)
    last = stats.get("last_order") or (orders[0] if orders else None)
    data_thin = bool(detail.get("data_thin") or ai.get("data_thin"))
    intro = _seller_intro(seller_name)
    fact_hint = ""
    if seller_facts:
        # Use first short clause of seller facts as optional colour (no invention)
        clause = re.split(r"[.\n;]", seller_facts, maxsplit=1)[0].strip()
        if 8 <= len(clause) <= 80:
            fact_hint = f" {clause}."

    greeting = f"Здравствуйте, {first}!"
    if data_thin or not orders:
        message = (
            f"{greeting} {intro}{fact_hint} "
            f"Если планируете букет или доставку — напишите, подберём спокойно под ваш повод."
        )
        notes = "Мало фактов в карточке — без ссылок на заказы и скидки."
    else:
        last_bit = ""
        if last:
            human_d = _format_human_date(last.get("date"))
            snip = _natural_product_bit(last.get("product_snippet"))
            if human_d and snip:
                last_bit = f" В прошлый раз ({human_d}) у вас были {snip.lower()}."
            elif human_d:
                last_bit = f" Мы уже помогали вам с букетом {human_d}."
            elif snip:
                last_bit = f" В прошлый раз у вас были {snip.lower()}."
        check_bit = ""
        if avg >= 1000:
            check_bit = f" Можем ориентироваться на привычный для вас уровень — около {avg:.0f} ₽."
        occasion = ""
        intent = str(ai.get("occasion_intent") or "")
        if "март" in intent.lower() or "8" in intent:
            occasion = " Если снова ждёте весенний повод — с радостью подготовим букет заранее."
        elif "валентин" in intent.lower() or "феврал" in intent.lower():
            occasion = " Если близок февральский повод — напишите, забронируем состав."
        elif "сентябр" in intent.lower() or "1 сентября" in intent.lower():
            occasion = " Если актуален сентябрьский повод — подскажу варианты заранее."
        message = (
            f"{greeting} {intro}{fact_hint}{last_bit}{check_bit}{occasion} "
            f"Напишите, если удобно — подберём что-то тёплое специально для вас."
        )
        notes = "Опирается на даты/суммы/сниппеты из кэша заказов; скидки не выдуманы."

    return {
        "message": " ".join(message.split()),
        "grounding_notes": notes,
        "source": "heuristic",
        "channel": (channel or "telegram").strip().lower(),
        "facts": facts_panel(detail),
        "seller_name": seller_name,
        "seller_facts": seller_facts,
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


def _strip_channel_trailer(message: str, channel: str) -> str:
    """Remove trailing channel labels that may disagree with selected channel."""
    msg = (message or "").rstrip()
    labels = ["WhatsApp", "Telegram", "Telegram-канал", "канал: WhatsApp", "канал: Telegram"]
    for lab in labels:
        for pat in (f"({lab})", f"（{lab}）"):
            if msg.endswith(pat):
                msg = msg[: -len(pat)].rstrip(" -—·,;")
    # Also strip wrong-channel mention in trailer only
    ch = (channel or "").strip().lower()
    wrong = []
    if ch.startswith("telegram"):
        wrong = ["WhatsApp"]
    elif ch == "whatsapp":
        wrong = ["Telegram", "Telegram-канал"]
    for lab in wrong:
        msg = re.sub(rf"\s*\({re.escape(lab)}\)\s*$", "", msg, flags=re.IGNORECASE)
    return msg.strip()


def generate_outreach_message(
    detail: dict[str, Any],
    *,
    channel: str = "telegram",
    refresh_ai: bool = False,
    seller_name: str = "",
    seller_facts: str = "",
) -> dict[str, Any]:
    """Generate editable outreach text; fall back to heuristic on LLM failure."""
    channel = (channel or "telegram").strip().lower()
    seller_name, seller_facts = normalize_seller_fields(seller_name, seller_facts)
    if refresh_ai or not (detail.get("ai") or {}).get("recommendation"):
        detail = {**detail, "ai": generate_ai_for_detail(detail)}

    fallback = heuristic_outreach_message(
        detail,
        channel=channel,
        seller_name=seller_name,
        seller_facts=seller_facts,
    )
    facts = _facts_payload(detail)
    payload = {
        "channel": channel,
        "channel_label": _channel_label(channel),
        "seller_name": seller_name,
        "seller_facts": seller_facts,
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
        f"Канал отправки: {_channel_label(channel)} "
        "(не дублируй название канала в конце сообщения).\n"
        f"Подпись продавца: {seller_name or '(не задана — мягко из цветочного магазина)'}.\n"
        f"Факты о магазине: {seller_facts or '(нет)'}.\n"
        "JSON фактов (единственный источник истины по клиенту):\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
    try:
        from agent.auxiliary_client import call_llm, extract_content_or_reasoning

        response = call_llm(
            task="compression",
            messages=[
                {
                    "role": "system",
                    "content": _OUTREACH_SYSTEM(seller_name, seller_facts),
                },
                {"role": "user", "content": user},
            ],
            max_tokens=700,
            temperature=0.35,
            timeout=45.0,
        )
        text = (extract_content_or_reasoning(response) or "").strip()
        parsed = _parse_outreach_json(text)
        if not parsed:
            log.warning("moysklad outreach AI: empty/unparsed, using heuristic")
            return fallback
        message = _strip_channel_trailer(parsed["message"], channel)
        return {
            "message": message,
            "grounding_notes": parsed.get("grounding_notes")
            or fallback["grounding_notes"],
            "source": "llm",
            "channel": channel,
            "facts": facts_panel(detail),
            "ai": detail.get("ai"),
            "seller_name": seller_name,
            "seller_facts": seller_facts,
        }
    except Exception as exc:
        log.warning("moysklad outreach AI unavailable: %s", exc)
        return {**fallback, "error": str(exc), "ai": detail.get("ai")}


def rewrite_outreach_message(
    draft: str,
    *,
    channel: str = "telegram",
    seller_name: str = "",
    seller_facts: str = "",
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Rewrite existing draft to be more sales-oriented and human."""
    channel = (channel or "telegram").strip().lower()
    seller_name, seller_facts = normalize_seller_fields(seller_name, seller_facts)
    draft = (draft or "").strip()
    if not draft:
        return {
            "message": "",
            "grounding_notes": "Пустой черновик — нечего переписывать.",
            "source": "empty",
            "channel": channel,
            "seller_name": seller_name,
            "seller_facts": seller_facts,
            "facts": facts_panel(detail) if detail else {},
        }

    # Heuristic soft cleanup when LLM unavailable
    def _heuristic_rewrite(text: str) -> str:
        t = text
        t = re.sub(
            r"\s*[—-]?\s*без навязанных скидок[^.]*(?:\.|$)",
            ".",
            t,
            flags=re.IGNORECASE,
        )
        t = re.sub(
            r"\s*[—-]?\s*только по вашей истории[^.]*(?:\.|$)",
            ".",
            t,
            flags=re.IGNORECASE,
        )
        t = re.sub(
            r"\s*[—-]?\s*без выдуманных скидок[^.]*(?:\.|$)",
            ".",
            t,
            flags=re.IGNORECASE,
        )
        t = re.sub(
            r"Ориентир по прошлым заказам\s*≈?\s*([\d\s]+)\s*₽\.?",
            r"Можем ориентироваться примерно на \1 ₽.",
            t,
            flags=re.IGNORECASE,
        )
        t = re.sub(
            r"Последний заказ у нас был (\d{4}-\d{2}-\d{2})",
            lambda m: f"Мы уже помогали вам с букетом {_format_human_date(m.group(1))}"
            if _format_human_date(m.group(1))
            else m.group(0),
            t,
        )
        t = re.sub(r"\s*\((?:з[-\s]?)?\d{1,6}(?:[-\s/]\d{1,6})?\)", "", t, flags=re.I)
        t = _strip_channel_trailer(t, channel)
        if seller_name and "это iris" in t.lower() and "iris" not in seller_name.lower():
            t = re.sub(r"Это Iris\.?", _seller_intro(seller_name), t, flags=re.IGNORECASE)
        t = re.sub(r"\s{2,}", " ", t)
        t = re.sub(r"\.\s*\.", ".", t)
        return t.strip()

    fallback_msg = _heuristic_rewrite(draft)
    facts_block = facts_panel(detail) if detail else {}
    user = (
        "Перепиши черновик продающе и по-человечески.\n"
        f"Канал: {_channel_label(channel)}.\n"
        f"Подпись продавца: {seller_name or '(не задана)'}.\n"
        f"Факты о магазине: {seller_facts or '(нет)'}.\n"
    )
    if detail:
        user += (
            "Факты клиента (не выдумывай сверх них):\n"
            + json.dumps(
                {
                    "client": (detail.get("client") or {}),
                    "orders": (detail.get("orders") or [])[:5],
                    "ai": {
                        "recommendation": (detail.get("ai") or {}).get("recommendation"),
                        "occasion_intent": (detail.get("ai") or {}).get(
                            "occasion_intent"
                        ),
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
    user += f"Исходный черновик:\n{draft}"

    try:
        from agent.auxiliary_client import call_llm, extract_content_or_reasoning

        response = call_llm(
            task="compression",
            messages=[
                {"role": "system", "content": _REWRITE_SYSTEM},
                {"role": "user", "content": user},
            ],
            max_tokens=700,
            temperature=0.4,
            timeout=45.0,
        )
        text = (extract_content_or_reasoning(response) or "").strip()
        parsed = _parse_outreach_json(text)
        if not parsed:
            log.warning("moysklad outreach rewrite: empty/unparsed, heuristic cleanup")
            return {
                "message": fallback_msg,
                "grounding_notes": "Heuristic cleanup (LLM parse failed).",
                "source": "heuristic_rewrite",
                "channel": channel,
                "facts": facts_block,
                "seller_name": seller_name,
                "seller_facts": seller_facts,
            }
        return {
            "message": _strip_channel_trailer(parsed["message"], channel),
            "grounding_notes": parsed.get("grounding_notes")
            or "Переписано с сохранением фактов.",
            "source": "llm_rewrite",
            "channel": channel,
            "facts": facts_block,
            "seller_name": seller_name,
            "seller_facts": seller_facts,
            "ai": (detail or {}).get("ai"),
        }
    except Exception as exc:
        log.warning("moysklad outreach rewrite unavailable: %s", exc)
        return {
            "message": fallback_msg,
            "grounding_notes": "Heuristic cleanup (LLM unavailable).",
            "source": "heuristic_rewrite",
            "channel": channel,
            "facts": facts_block,
            "seller_name": seller_name,
            "seller_facts": seller_facts,
            "error": str(exc),
            "ai": (detail or {}).get("ai"),
        }


def build_outreach_for_row(
    row: dict[str, Any],
    *,
    channel: str = "telegram",
    refresh_ai: bool = True,
    seller_name: str = "",
    seller_facts: str = "",
) -> dict[str, Any]:
    """Catalog row → detail → outreach draft payload."""
    detail = build_client_detail(row)
    result = generate_outreach_message(
        detail,
        channel=channel,
        refresh_ai=refresh_ai,
        seller_name=seller_name,
        seller_facts=seller_facts,
    )
    result["detail_ok"] = True
    result["client_id"] = (detail.get("client") or {}).get("id")
    result["client_name"] = (detail.get("client") or {}).get("name")
    return result
