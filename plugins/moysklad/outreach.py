"""AI outreach draft text for MoySklad campaigns (Telegram / WhatsApp).

Grounded on the same client-card facts + recommendation guardrails.
Never invents discounts, orders, phones, or VIP status.
Seller identity comes from editable shop settings (not hardcoded Iris).
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Iterator, Optional

from plugins.moysklad.client_card import (
    _facts_payload,
    build_client_detail,
    build_fact_blocks,
    compute_risks,
    heuristic_ai,
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
1. Используй ТОЛЬКО факты из JSON (client/orders/ai/risks) + подпись/факты продавца выше.
   Ничего не выдумывай: скидки, промокоды, акции, телефоны, заказы, даты, суммы,
   VIP, баллы, адреса, долг — если нет в данных, не упоминай.
2. Не обещай наличие конкретных букетов/SKU, которых нет в сниппетах заказов.
3. Тон: живой, тёплый, КРЕАТИВНЫЙ — разные формулировки каждый раз, мягкая образность
   цветов, без шаблонного канцелярита и без роботского каркаса. Не копируй один
   и тот же скелет («Здравствуйте… Это… Последний заказ… Напишите»).
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
9. РИСКИ / ДОЛГ: если risks.has_debt / unpaid_order_count / do_not_upsell —
   НЕ предлагай букеты, премиум, подарки, скидки, новые заказы. Пиши про сверку
   оплаты / напоминание закрыть задолженность. Долг не выдумывай.
10. Ответ — строго JSON без markdown:
{{"message": "текст сообщения клиенту", "grounding_notes": "1-2 предложения: на каких фактах основан текст"}}
"""


_REWRITE_SYSTEM = """Ты — редактор исходящих сообщений цветочного магазина.
Перепиши черновик ПРОДАЮЩЕ и по-человечески: ясная ценность, тёплый тон,
мягкий призыв к ответу, без роботского канцелярита и без грубости.
Отвечай строго на русском. Перепиши текст ЗАНОВО (не лёгкая правка пары слов) —
сохрани факты, усили продающую подачу.

ЖЁСТКИЕ ПРАВИЛА:
1. Не выдумывай скидки, промокоды, акции, заказы, даты, суммы, VIP, адреса, долг.
2. Сохраняй смысл и все реальные факты из исходного текста; можно смягчить формулировки.
3. Убери мета-фразы («без навязанных скидок», «только по истории»), сырые ISO-даты,
   внутренние коды заказов, ярлыки канала в скобках в конце.
4. Используй подпись/факты продавца из user prompt, если они есть.
5. Канал — как указано; не меняй канал и не дописывай «(WhatsApp)»/«(Telegram)».
6. 2–5 коротких предложений, живой тон переписки, продающий без давления.
7. Если в фактах клиента есть долг / неоплата / do_not_upsell — убери upsell букетов/
   подарков/скидок и переориентируй текст на сверку оплаты (не выдумывай сумму долга).
8. Ответ — строго JSON без markdown:
{"message": "новый текст", "grounding_notes": "что сохранили / что усилили в продаже"}
"""


_SANITY_SYSTEM = """Ты — контролёр смысла исходящих сообщений цветочного магазина.
Проверь черновик здравым смыслом продавца относительно фактов клиента.
Интерес клиента и честность важнее «продать ещё букет». Отвечай на русском.

ПРАВИЛА:
1. Если у клиента долг / неоплаченные заказы / do_not_upsell — сообщение НЕ должно
   предлагать букеты, премиум, цветы, подарки, скидки, акции, «сюрприз», новый заказ.
   Нужно мягко спросить про оплату / сверку / когда удобно закрыть задолженность.
2. Не выдумывай долг, суммы, заказы — только то, что в JSON risks/facts.
3. Другие здравые проверки: не обещай VIP/скидку/наличие SKU, которых нет в фактах;
   не противоречь рискам; не дави и не стыди клиента.
4. Если текст ок — ok=true, issues=[], revised_text=null.
5. Если нет — ok=false, issues=[краткие причины], revised_text=исправленный текст
   (2–5 предложений, тот же канал/подпись; при долге — только оплата/сверка).
6. Ответ — строго JSON без markdown:
{"ok": true, "issues": [], "revised_text": null}
"""


_BOUQUET_SYSTEM = """Ты — флорист-консультант цветочного магазина.
Задача: предложить клиенту КОНКРЕТНЫЙ букет из ЕГО истории заказов
(название/состав из product_snippet в JSON). Не абстрактный «букет», а именно
тот, что уже был — или явно названный вариант из списка исторических сниппетов.
Отвечай строго на русском.

ЖЁСТКИЕ ПРАВИЛА:
1. Только факты из JSON orders/client/risks + подпись продавца. Никаких выдуманных
   названий букетов, скидок, акций, наличия на складе.
2. В сообщении ОБЯЗАТЕЛЬНО назови конкретный исторический букет/состав словами
   из product_snippet (можно слегка смягчить падеж, но не меняй суть).
3. Мягко предложи повторить / собрать похожий; 2–5 предложений; обращение по имени.
4. Не хардкодь «Это Iris», если подпись другая. Не ярлык канала в конце.
5. РИСКИ / ДОЛГ (do_not_upsell / has_debt): НЕ предлагай букет — только сверка оплаты.
6. Ответ — строго JSON без markdown:
{"message": "текст", "grounding_notes": "какой сниппет/заказ взят"}
"""


_PARAPHRASE_SYSTEM = """Ты — лингвист-редактор. Сделай ПОЛНУЮ ПАРАФРАЗУ черновика.
Это НЕ «Сгенерировать AI» (не пиши новое креативное письмо с нуля по карточке)
и НЕ «Продающе и по-человечески» (не усиливай продажи / CTA / ценность).

Цель: тот же смысл и факты, но ДРУГОЙ текст — другая структура предложений,
другие слова, другой порядок мыслей. Читатель должен сразу видеть, что формулировки
сменились. Запрещено оставлять фразы почти дословно.

Отвечай строго на русском.

ЖЁСТКИЕ ПРАВИЛА:
1. Сохрани все факты (имя, даты, суммы, букеты, долг) — ничего не выдумывай.
2. Замени почти каждую фразу; поменяй порядок предложений, где уместно.
3. Не делай текст «продающее» и не добавляй новый CTA ради продаж.
4. Не копируй шаблон «Здравствуйте… Это… Последний заказ… Напишите».
5. 2–5 предложений. Без ярлыка канала в конце. Без markdown.
6. Если в фактах долг / do_not_upsell — не предлагай букеты; оставь сверку оплаты.
7. Ответ — строго JSON без markdown:
{"message": "полностью перефразированный текст", "grounding_notes": "что сохранили, как сменили форму"}
"""

# Creative generate / sales rewrite temperatures (OpenRouter DeepSeek flash).
OUTREACH_GENERATE_TEMPERATURE = 0.85
OUTREACH_REWRITE_TEMPERATURE = 0.95
OUTREACH_BOUQUET_TEMPERATURE = 0.7
OUTREACH_PARAPHRASE_TEMPERATURE = 0.9
OUTREACH_SANITY_TEMPERATURE = 0.1

_UPSELL_FLOWER_RE = re.compile(
    r"(букет|роз[аыуе]|пион|композиц|премиум|дорогост|дорог(ой|ие|ая)|"
    r"цвет(ы|ов|очн)|флорист|корзин[аыуе]|заброн|подбер(ём|ем)|"
    r"подар(ок|ки)|сюрприз|скидк|акци[яи]|бонус|промо|"
    r"ориентир.*₽|привычн.*(уровен|чек))",
    re.IGNORECASE,
)


def normalize_seller_fields(
    seller_name: str | None = None,
    seller_facts: str | None = None,
) -> tuple[str, str]:
    name = str(seller_name or "").strip()
    facts = str(seller_facts or "").strip()
    return name, facts


def _conversation_facts(detail: dict[str, Any]) -> dict[str, Any]:
    """TG / WA thread snippet for facts panel + AI grounding."""
    conv = detail.get("conversation")
    if not isinstance(conv, dict) or not conv.get("messages"):
        try:
            from plugins.moysklad.conversations import conversation_for_detail

            conv = conversation_for_detail(detail)
        except Exception:
            conv = {"messages": [], "message_count": 0, "preview": "", "empty": True}
    messages = list(conv.get("messages") or [])
    preview_msgs = []
    for m in messages[-12:]:
        preview_msgs.append(
            {
                "direction": m.get("direction"),
                "channel": m.get("channel"),
                "label": m.get("label"),
                "text": m.get("text"),
                "ts": m.get("ts"),
            }
        )
    return {
        "preview": conv.get("preview") or "",
        "message_count": int(conv.get("message_count") or len(messages)),
        "messages": preview_msgs,
        "empty": not preview_msgs,
    }


def facts_panel(detail: dict[str, Any]) -> dict[str, Any]:
    """Compact grounded facts for the seller audit side panel."""
    client = detail.get("client") or {}
    stats = detail.get("stats") or {}
    messaging = detail.get("messaging") or {}
    ai = detail.get("ai") or {}
    orders = list(detail.get("orders") or [])
    last = stats.get("last_order") or (orders[0] if orders else None)
    buckets = client.get("tag_buckets") or {}
    risks = detail.get("risks") or compute_risks(
        client, orders, data_thin=bool(detail.get("data_thin"))
    )
    blocks = detail.get("fact_blocks") or build_fact_blocks(
        {**detail, "risks": risks}
    )
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
                "unpaid": o.get("unpaid"),
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
        "risks": {
            "has_debt": bool(risks.get("has_debt")),
            "debt_amount": risks.get("debt_amount"),
            "balance": risks.get("balance"),
            "unpaid_order_count": int(risks.get("unpaid_order_count") or 0),
            "unpaid_total": risks.get("unpaid_total"),
            "do_not_upsell": bool(risks.get("do_not_upsell")),
            "flags": list(risks.get("flags") or []),
        },
        # Three structured audit blocks (aligned with AI card sections).
        "block_history_profile": blocks.get("history_profile"),
        "block_occasion_intent": blocks.get("occasion_intent"),
        "block_risks": blocks.get("risks"),
        "fact_blocks": blocks,
        "conversation": _conversation_facts(detail),
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


def _payment_reminder_message(
    detail: dict[str, Any],
    *,
    seller_name: str = "",
) -> str:
    """Soft reconcile/payment nudge from grounded risk facts only."""
    client = detail.get("client") or {}
    risks = detail.get("risks") or compute_risks(
        client,
        list(detail.get("orders") or []),
        data_thin=bool(detail.get("data_thin")),
    )
    name = str(client.get("name") or "").strip() or "клиент"
    first = name.split()[0] if name else "клиент"
    intro = _seller_intro(seller_name)
    bits: list[str] = []
    if risks.get("has_debt") and risks.get("debt_amount") is not None:
        bits.append(f"по балансу открыта сумма ≈ {float(risks['debt_amount']):.0f} ₽")
    if risks.get("unpaid_order_count"):
        bits.append(
            f"есть незакрытые оплаты по заказам "
            f"(≈ {float(risks.get('unpaid_total') or 0):.0f} ₽)"
        )
    detail_bit = (" " + ", ".join(bits) + ".") if bits else ""
    return (
        f"Здравствуйте, {first}! {intro} "
        f"Хотели мягко свериться по оплате{detail_bit} "
        f"Напишите, пожалуйста, когда будет удобно закрыть вопрос — "
        f"сначала сверка, без новых продаж, пока не сверимся."
    )


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
    risks = detail.get("risks") or compute_risks(client, orders, data_thin=data_thin)
    intro = _seller_intro(seller_name)
    fact_hint = ""
    if seller_facts:
        # Use first short clause of seller facts as optional colour (no invention)
        clause = re.split(r"[.\n;]", seller_facts, maxsplit=1)[0].strip()
        if 8 <= len(clause) <= 80:
            fact_hint = f" {clause}."

    greeting = f"Здравствуйте, {first}!"
    if risks.get("do_not_upsell"):
        message = _payment_reminder_message(detail, seller_name=seller_name)
        notes = "Риск/долг в фактах — без upsell букетов, только сверка оплаты."
    elif data_thin or not orders:
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


def _risks_from_detail(detail: dict[str, Any] | None) -> dict[str, Any]:
    if not detail:
        return {}
    client = detail.get("client") or {}
    orders = list(detail.get("orders") or [])
    return detail.get("risks") or compute_risks(
        client, orders, data_thin=bool(detail.get("data_thin"))
    )


def _parse_sanity_json(text: str) -> Optional[dict[str, Any]]:
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
    if not isinstance(data, dict) or "ok" not in data:
        return None
    issues_raw = data.get("issues") or []
    issues = [str(x).strip() for x in issues_raw if str(x).strip()]
    revised = data.get("revised_text")
    revised_s = str(revised).strip() if revised else None
    return {
        "ok": bool(data.get("ok")),
        "issues": issues,
        "revised_text": revised_s or None,
    }


def heuristic_sanity_check(
    message: str,
    detail: dict[str, Any] | None,
    *,
    seller_name: str = "",
) -> dict[str, Any]:
    """Regex/rules fallback: debt/risk ⇒ reject flower upsell language."""
    msg = (message or "").strip()
    risks = _risks_from_detail(detail)
    do_not = bool(risks.get("do_not_upsell"))
    issues: list[str] = []
    revised: Optional[str] = None
    if do_not and msg and _UPSELL_FLOWER_RE.search(msg):
        issues.append(
            "При долге/неоплате нельзя предлагать букеты, подарки или акции — "
            "сначала сверка оплаты."
        )
        revised = _payment_reminder_message(
            detail or {}, seller_name=seller_name
        )
    return {
        "ok": not issues,
        "issues": issues,
        "revised_text": revised,
        "source": "heuristic",
    }


def sanity_check_outreach_message(
    message: str,
    detail: dict[str, Any] | None = None,
    *,
    channel: str = "telegram",
    seller_name: str = "",
    seller_facts: str = "",
    use_llm: bool = True,
) -> dict[str, Any]:
    """Second-pass sanity check on outreach text.

    ``use_llm=False`` → heuristic only (fast path after generate/rewrite).
    Explicit «Проверить смысл» keeps ``use_llm=True``.
    """
    channel = (channel or "telegram").strip().lower()
    seller_name, seller_facts = normalize_seller_fields(seller_name, seller_facts)
    msg = (message or "").strip()
    fallback = heuristic_sanity_check(msg, detail, seller_name=seller_name)
    if not msg:
        return {
            "ok": False,
            "issues": ["Пустой текст — нечего проверять."],
            "revised_text": None,
            "source": "empty",
        }
    if not use_llm:
        return fallback

    risks = _risks_from_detail(detail)
    panel = facts_panel(detail) if detail else {}
    user = (
        "Проверь смысл черновика относительно фактов клиента.\n"
        f"Канал: {_channel_label(channel)}.\n"
        f"Подпись продавца: {seller_name or '(не задана)'}.\n"
        f"Факты о магазине: {seller_facts or '(нет)'}.\n"
        "Риски (единственный источник по долгу):\n"
        + json.dumps(
            {
                "has_debt": bool(risks.get("has_debt")),
                "debt_amount": risks.get("debt_amount"),
                "unpaid_order_count": risks.get("unpaid_order_count"),
                "unpaid_total": risks.get("unpaid_total"),
                "do_not_upsell": bool(risks.get("do_not_upsell")),
                "flags": list(risks.get("flags") or []),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\nКраткие факты клиента:\n"
        + json.dumps(
            {
                "name": panel.get("name"),
                "order_count": panel.get("order_count"),
                "avg_check": panel.get("avg_check"),
                "event_tags": panel.get("event_tags"),
            },
            ensure_ascii=False,
            indent=2,
        )
        + f"\nЧерновик:\n{msg}"
    )
    try:
        from agent.auxiliary_client import call_llm, extract_content_or_reasoning

        response = call_llm(
            task="compression",
            messages=[
                {"role": "system", "content": _SANITY_SYSTEM},
                {"role": "user", "content": user},
            ],
            max_tokens=700,
            temperature=OUTREACH_SANITY_TEMPERATURE,
            timeout=40.0,
        )
        text = (extract_content_or_reasoning(response) or "").strip()
        parsed = _parse_sanity_json(text)
        if not parsed:
            log.warning("moysklad outreach sanity: empty/unparsed, heuristic")
            return fallback
        revised = parsed.get("revised_text")
        if revised:
            revised = _strip_channel_trailer(str(revised), channel)
        # Never let LLM invent debt: if heuristic says ok but LLM invents issues
        # without do_not_upsell — trust heuristic empty-risk path.
        if not risks.get("do_not_upsell") and not parsed["ok"]:
            # Still allow non-debt issues from LLM, but drop debt-only rewrites
            debtish = any(
                re.search(r"долг|неоплат|задолжен", i, re.I)
                for i in parsed.get("issues") or []
            )
            if debtish and not _UPSELL_FLOWER_RE.search(msg):
                return {
                    "ok": True,
                    "issues": [],
                    "revised_text": None,
                    "source": "llm_guarded",
                }
        return {
            "ok": bool(parsed["ok"]),
            "issues": list(parsed.get("issues") or []),
            "revised_text": revised,
            "source": "llm",
        }
    except Exception as exc:
        log.warning("moysklad outreach sanity unavailable: %s", exc)
        return {**fallback, "error": str(exc)}


def _apply_sanity(
    result: dict[str, Any],
    detail: dict[str, Any] | None,
    *,
    channel: str,
    seller_name: str,
    seller_facts: str,
    auto_revise: bool = True,
    use_llm_sanity: bool = False,
) -> dict[str, Any]:
    # Default: heuristic only. Generate/rewrite already spent one LLM round-trip;
    # a second LLM sanity pass made «Сгенерировать AI» ~2–3× slower than chat.
    # Full LLM sanity stays on POST /campaigns/sanity («Проверить смысл»).
    sanity = sanity_check_outreach_message(
        str(result.get("message") or ""),
        detail,
        channel=channel,
        seller_name=seller_name,
        seller_facts=seller_facts,
        use_llm=use_llm_sanity,
    )
    out = {**result, "sanity": sanity}
    if (
        auto_revise
        and not sanity.get("ok")
        and sanity.get("revised_text")
    ):
        out["message"] = sanity["revised_text"]
        out["sanity"] = {**sanity, "auto_revised": True}
        notes = str(out.get("grounding_notes") or "")
        fix_note = "Sanity: текст скорректирован (долг/риск — без upsell)."
        out["grounding_notes"] = (notes + " " + fix_note).strip() if notes else fix_note
    return out


def generate_outreach_message(
    detail: dict[str, Any],
    *,
    channel: str = "telegram",
    refresh_ai: bool = False,
    seller_name: str = "",
    seller_facts: str = "",
) -> dict[str, Any]:
    """Generate editable outreach text; fall back to heuristic on LLM failure.

    Latency: one LLM call for the message. Card-level AI and LLM sanity are
    not chained here (chat feels fast because it is one streamed call;
    previously generate ran card AI + message + sanity = 3 serial calls).
    """
    channel = (channel or "telegram").strip().lower()
    seller_name, seller_facts = normalize_seller_fields(seller_name, seller_facts)
    if not (detail.get("ai") or {}).get("recommendation"):
        # Heuristic card AI is enough for the prompt payload. Full LLM card
        # summary is a separate «Обновить AI» action — do not block outreach.
        client = detail.get("client") or {}
        orders = list(detail.get("orders") or [])
        data_thin = bool(detail.get("data_thin"))
        risks = detail.get("risks") or compute_risks(
            client, orders, data_thin=data_thin
        )
        detail = {
            **detail,
            "ai": heuristic_ai(
                client,
                orders,
                vip=bool(client.get("vip")),
                loyalty=client.get("loyalty_points"),
                data_thin=data_thin,
                risks=risks,
            ),
        }
    # refresh_ai only means "new creative message" — ignore for card LLM.
    _ = refresh_ai

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
        "risks": facts.get("risks"),
        "conversation": _conversation_facts(detail),
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
        "Сгенерируй КРЕАТИВНЫЙ текст личного сообщения клиенту "
        "(живая формулировка, не шаблон).\n"
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
            temperature=OUTREACH_GENERATE_TEMPERATURE,
            timeout=45.0,
        )
        text = (extract_content_or_reasoning(response) or "").strip()
        parsed = _parse_outreach_json(text)
        if not parsed:
            log.warning("moysklad outreach AI: empty/unparsed, using heuristic")
            return _apply_sanity(
                fallback,
                detail,
                channel=channel,
                seller_name=seller_name,
                seller_facts=seller_facts,
            )
        message = _strip_channel_trailer(parsed["message"], channel)
        result = {
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
        return _apply_sanity(
            result,
            detail,
            channel=channel,
            seller_name=seller_name,
            seller_facts=seller_facts,
        )
    except Exception as exc:
        log.warning("moysklad outreach AI unavailable: %s", exc)
        return _apply_sanity(
            {**fallback, "error": str(exc), "ai": detail.get("ai")},
            detail,
            channel=channel,
            seller_name=seller_name,
            seller_facts=seller_facts,
        )


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

    risks = _risks_from_detail(detail)
    if risks.get("do_not_upsell") and _UPSELL_FLOWER_RE.search(draft):
        # Debt + flower upsell draft → rewrite toward payment reconcile first.
        fallback_msg = _payment_reminder_message(
            detail or {}, seller_name=seller_name
        )
    else:
        fallback_msg = _heuristic_rewrite(draft)
    facts_block = facts_panel(detail) if detail else {}
    user = (
        "Перепиши черновик ЗАНОВО — продающе и по-человечески "
        "(сильнее ценность и мягкий CTA, без выдуманных акций).\n"
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
                    "risks": {
                        "has_debt": bool(risks.get("has_debt")),
                        "debt_amount": risks.get("debt_amount"),
                        "unpaid_order_count": risks.get("unpaid_order_count"),
                        "do_not_upsell": bool(risks.get("do_not_upsell")),
                        "flags": list(risks.get("flags") or []),
                    },
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
            temperature=OUTREACH_REWRITE_TEMPERATURE,
            timeout=45.0,
        )
        text = (extract_content_or_reasoning(response) or "").strip()
        parsed = _parse_outreach_json(text)
        if not parsed:
            log.warning("moysklad outreach rewrite: empty/unparsed, heuristic cleanup")
            return _apply_sanity(
                {
                    "message": fallback_msg,
                    "grounding_notes": "Heuristic cleanup (LLM parse failed).",
                    "source": "heuristic_rewrite",
                    "channel": channel,
                    "facts": facts_block,
                    "seller_name": seller_name,
                    "seller_facts": seller_facts,
                },
                detail,
                channel=channel,
                seller_name=seller_name,
                seller_facts=seller_facts,
            )
        return _apply_sanity(
            {
                "message": _strip_channel_trailer(parsed["message"], channel),
                "grounding_notes": parsed.get("grounding_notes")
                or "Переписано с сохранением фактов.",
                "source": "llm_rewrite",
                "channel": channel,
                "facts": facts_block,
                "seller_name": seller_name,
                "seller_facts": seller_facts,
                "ai": (detail or {}).get("ai"),
            },
            detail,
            channel=channel,
            seller_name=seller_name,
            seller_facts=seller_facts,
        )
    except Exception as exc:
        log.warning("moysklad outreach rewrite unavailable: %s", exc)
        return _apply_sanity(
            {
                "message": fallback_msg,
                "grounding_notes": "Heuristic cleanup (LLM unavailable).",
                "source": "heuristic_rewrite",
                "channel": channel,
                "facts": facts_block,
                "seller_name": seller_name,
                "seller_facts": seller_facts,
                "error": str(exc),
                "ai": (detail or {}).get("ai"),
            },
            detail,
            channel=channel,
            seller_name=seller_name,
            seller_facts=seller_facts,
        )


def _normalize_compare(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _too_similar(a: str, b: str, *, threshold: float = 0.82) -> bool:
    na, nb = _normalize_compare(a), _normalize_compare(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= threshold


def _historical_bouquet_candidates(detail: dict[str, Any]) -> list[dict[str, Any]]:
    """Concrete bouquets from order history (natural product snippets only)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for order in list(detail.get("orders") or []):
        if not isinstance(order, dict):
            continue
        snip = _natural_product_bit(
            order.get("product_snippet") or order.get("name") or ""
        )
        if not snip:
            continue
        key = snip.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "product": snip,
                "date": order.get("date"),
                "date_human": _format_human_date(order.get("date")),
                "sum": order.get("sum"),
            }
        )
        if len(out) >= 8:
            break
    return out


def heuristic_bouquet_suggestion(
    detail: dict[str, Any],
    *,
    channel: str = "telegram",
    seller_name: str = "",
    seller_facts: str = "",
) -> dict[str, Any]:
    """Deterministic suggestion naming a real historical bouquet."""
    seller_name, seller_facts = normalize_seller_fields(seller_name, seller_facts)
    channel = (channel or "telegram").strip().lower()
    client = detail.get("client") or {}
    name = str(client.get("name") or "").strip() or "клиент"
    first = name.split()[0] if name else "клиент"
    risks = _risks_from_detail(detail)
    intro = _seller_intro(seller_name)
    if risks.get("do_not_upsell"):
        msg = _payment_reminder_message(detail, seller_name=seller_name)
        return {
            "message": msg,
            "grounding_notes": "Долг/риск — без предложения букета.",
            "source": "heuristic_bouquet",
            "channel": channel,
            "facts": facts_panel(detail),
            "seller_name": seller_name,
            "seller_facts": seller_facts,
            "ai": detail.get("ai"),
            "bouquet": None,
        }
    cands = _historical_bouquet_candidates(detail)
    if not cands:
        msg = (
            f"Здравствуйте, {first}! {intro} "
            f"В карточке пока нет названия прошлого букета — "
            f"напишите, какой состав помните, подберём похожий."
        )
        notes = "Нет natural product_snippet в истории заказов."
        bouquet = None
    else:
        pick = cands[0]
        when = pick.get("date_human") or ""
        product = pick["product"]
        when_bit = f" ({when})" if when else ""
        msg = (
            f"Здравствуйте, {first}! {intro} "
            f"В прошлый раз{when_bit} у вас был «{product}». "
            f"Можем снова собрать именно его или очень похожий — напишите, если актуально."
        )
        notes = f"Исторический сниппет: {product}."
        bouquet = pick
    return {
        "message": _strip_channel_trailer(msg, channel),
        "grounding_notes": notes,
        "source": "heuristic_bouquet",
        "channel": channel,
        "facts": facts_panel(detail),
        "seller_name": seller_name,
        "seller_facts": seller_facts,
        "ai": detail.get("ai"),
        "bouquet": bouquet,
        "bouquet_candidates": cands,
    }


def heuristic_paraphrase(draft: str, *, channel: str = "telegram") -> str:
    """Force a visibly different wording without LLM."""
    text = _strip_channel_trailer((draft or "").strip(), channel)
    if not text:
        return ""
    # Split into sentences; reverse middle; soft synonym swaps.
    parts = [p.strip() for p in re.split(r"(?<=[.!?…])\s+", text) if p.strip()]
    if len(parts) >= 2:
        parts = [parts[0]] + list(reversed(parts[1:]))
    elif parts:
        parts = parts
    joined = " ".join(parts)
    swaps = (
        (r"\bЗдравствуйте\b", "Добрый день"),
        (r"\bДобрый день\b", "Здравствуйте"),
        (r"\bНапишите\b", "Ответьте, пожалуйста,"),
        (r"\bнапишите\b", "дайте знать"),
        (r"\bМожем\b", "Готовы"),
        (r"\bможем\b", "готовы"),
        (r"\bподберём\b", "соберём"),
        (r"\bПодберём\b", "Соберём"),
        (r"\bпожалуйста\b", "если удобно"),
    )
    out = joined
    for pat, rep in swaps:
        out2 = re.sub(pat, rep, out, count=1)
        if out2 != out:
            out = out2
            break
    if _too_similar(out, text, threshold=0.9):
        out = (
            f"Перефразируя: {out}" if not out.lower().startswith("перефразируя") else out
        )
        # Last resort: prefix + move last sentence first
        if len(parts) >= 2:
            out = " ".join([parts[-1]] + parts[:-1])
    return _strip_channel_trailer(out.strip(), channel)


def suggest_historical_bouquet_message(
    detail: dict[str, Any],
    *,
    channel: str = "telegram",
    seller_name: str = "",
    seller_facts: str = "",
) -> dict[str, Any]:
    """Propose a concrete bouquet from the client's order history."""
    channel = (channel or "telegram").strip().lower()
    seller_name, seller_facts = normalize_seller_fields(seller_name, seller_facts)
    detail = _prepare_generate_detail(detail)
    fallback = heuristic_bouquet_suggestion(
        detail,
        channel=channel,
        seller_name=seller_name,
        seller_facts=seller_facts,
    )
    risks = _risks_from_detail(detail)
    cands = list(fallback.get("bouquet_candidates") or _historical_bouquet_candidates(detail))
    payload = {
        "channel": channel,
        "channel_label": _channel_label(channel),
        "seller_name": seller_name,
        "seller_facts": seller_facts,
        "client": (detail.get("client") or {}),
        "historical_bouquets": cands,
        "orders": (detail.get("orders") or [])[:8],
        "risks": {
            "has_debt": bool(risks.get("has_debt")),
            "do_not_upsell": bool(risks.get("do_not_upsell")),
            "flags": list(risks.get("flags") or []),
        },
        "ai": {
            "recommendation": (detail.get("ai") or {}).get("recommendation"),
            "occasion_intent": (detail.get("ai") or {}).get("occasion_intent"),
        },
    }
    user = (
        "Предложи клиенту КОНКРЕТНЫЙ букет из historical_bouquets "
        "(обязательно назови продукт из списка).\n"
        f"Канал: {_channel_label(channel)}.\n"
        f"Подпись: {seller_name or '(не задана)'}.\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
    try:
        from agent.auxiliary_client import call_llm, extract_content_or_reasoning

        response = call_llm(
            task="compression",
            messages=[
                {"role": "system", "content": _BOUQUET_SYSTEM},
                {"role": "user", "content": user},
            ],
            max_tokens=700,
            temperature=OUTREACH_BOUQUET_TEMPERATURE,
            timeout=45.0,
        )
        text = (extract_content_or_reasoning(response) or "").strip()
        parsed = _parse_outreach_json(text)
        if not parsed:
            return _apply_sanity(
                fallback,
                detail,
                channel=channel,
                seller_name=seller_name,
                seller_facts=seller_facts,
            )
        message = _strip_channel_trailer(parsed["message"], channel)
        # Prefer naming a known historical product; else keep heuristic.
        if cands and not risks.get("do_not_upsell"):
            named = any(
                str(c["product"]).casefold() in message.casefold() for c in cands
            )
            if not named:
                log.warning("moysklad bouquet AI omitted historical product; heuristic")
                return _apply_sanity(
                    fallback,
                    detail,
                    channel=channel,
                    seller_name=seller_name,
                    seller_facts=seller_facts,
                )
        result = {
            "message": message,
            "grounding_notes": parsed.get("grounding_notes")
            or fallback.get("grounding_notes")
            or "",
            "source": "llm_bouquet",
            "channel": channel,
            "facts": facts_panel(detail),
            "seller_name": seller_name,
            "seller_facts": seller_facts,
            "ai": detail.get("ai"),
            "bouquet": fallback.get("bouquet"),
            "bouquet_candidates": cands,
        }
        return _apply_sanity(
            result,
            detail,
            channel=channel,
            seller_name=seller_name,
            seller_facts=seller_facts,
        )
    except Exception as exc:
        log.warning("moysklad bouquet suggest unavailable: %s", exc)
        return _apply_sanity(
            {**fallback, "error": str(exc)},
            detail,
            channel=channel,
            seller_name=seller_name,
            seller_facts=seller_facts,
        )


def paraphrase_outreach_message(
    draft: str,
    *,
    channel: str = "telegram",
    seller_name: str = "",
    seller_facts: str = "",
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Full paraphrase — must differ from generate and sales-rewrite styles."""
    channel = (channel or "telegram").strip().lower()
    seller_name, seller_facts = normalize_seller_fields(seller_name, seller_facts)
    draft = (draft or "").strip()
    facts_block = facts_panel(detail) if detail else {}
    if not draft:
        return {
            "message": "",
            "grounding_notes": "Пустой черновик — нечего парафразировать.",
            "source": "empty",
            "channel": channel,
            "seller_name": seller_name,
            "seller_facts": seller_facts,
            "facts": facts_block,
        }
    risks = _risks_from_detail(detail)
    fallback_msg = heuristic_paraphrase(draft, channel=channel)
    if risks.get("do_not_upsell") and _UPSELL_FLOWER_RE.search(draft):
        fallback_msg = _payment_reminder_message(
            detail or {}, seller_name=seller_name
        )
    user = (
        "Сделай ПОЛНУЮ парафразу (не sales-rewrite и не generate с нуля).\n"
        f"Канал: {_channel_label(channel)}.\n"
        f"Подпись: {seller_name or '(не задана)'}.\n"
        f"Факты магазина: {seller_facts or '(нет)'}.\n"
    )
    if detail:
        user += (
            "Факты клиента:\n"
            + json.dumps(
                {
                    "client": (detail.get("client") or {}),
                    "orders": (detail.get("orders") or [])[:5],
                    "risks": {
                        "has_debt": bool(risks.get("has_debt")),
                        "do_not_upsell": bool(risks.get("do_not_upsell")),
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
                {"role": "system", "content": _PARAPHRASE_SYSTEM},
                {"role": "user", "content": user},
            ],
            max_tokens=700,
            temperature=OUTREACH_PARAPHRASE_TEMPERATURE,
            timeout=45.0,
        )
        text = (extract_content_or_reasoning(response) or "").strip()
        parsed = _parse_outreach_json(text)
        message = ""
        notes = ""
        if parsed:
            message = _strip_channel_trailer(parsed["message"], channel)
            notes = parsed.get("grounding_notes") or ""
        if not message or _too_similar(message, draft):
            # One stricter retry
            response2 = call_llm(
                task="compression",
                messages=[
                    {"role": "system", "content": _PARAPHRASE_SYSTEM},
                    {
                        "role": "user",
                        "content": user
                        + "\n\nПРЕДЫДУЩИЙ ОТВЕТ СЛИШКОМ ПОХОЖ. "
                        "Перепиши ещё раз — другие слова и другой порядок фраз.",
                    },
                ],
                max_tokens=700,
                temperature=min(1.0, OUTREACH_PARAPHRASE_TEMPERATURE + 0.05),
                timeout=45.0,
            )
            text2 = (extract_content_or_reasoning(response2) or "").strip()
            parsed2 = _parse_outreach_json(text2)
            if parsed2:
                message2 = _strip_channel_trailer(parsed2["message"], channel)
                if message2 and not _too_similar(message2, draft):
                    message = message2
                    notes = parsed2.get("grounding_notes") or notes
        if not message or _too_similar(message, draft):
            message = fallback_msg
            notes = (notes + " Heuristic paraphrase (similarity guard).").strip()
            source = "heuristic_paraphrase"
        else:
            source = "llm_paraphrase"
        return _apply_sanity(
            {
                "message": message,
                "grounding_notes": notes or "Полная парафраза с сохранением фактов.",
                "source": source,
                "channel": channel,
                "facts": facts_block,
                "seller_name": seller_name,
                "seller_facts": seller_facts,
                "ai": (detail or {}).get("ai"),
            },
            detail,
            channel=channel,
            seller_name=seller_name,
            seller_facts=seller_facts,
        )
    except Exception as exc:
        log.warning("moysklad paraphrase unavailable: %s", exc)
        return _apply_sanity(
            {
                "message": fallback_msg,
                "grounding_notes": "Heuristic paraphrase (LLM unavailable).",
                "source": "heuristic_paraphrase",
                "channel": channel,
                "facts": facts_block,
                "seller_name": seller_name,
                "seller_facts": seller_facts,
                "error": str(exc),
                "ai": (detail or {}).get("ai"),
            },
            detail,
            channel=channel,
            seller_name=seller_name,
            seller_facts=seller_facts,
        )


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


# ---------------------------------------------------------------------------
# Streaming / batch (NDJSON events for dashboard UI)
# ---------------------------------------------------------------------------

_MESSAGE_JSON_START = re.compile(r'"message"\s*:\s*"')


class ProgressiveJsonMessage:
    """Pull the ``message`` string out of a partial JSON LLM stream."""

    def __init__(self) -> None:
        self.raw = ""
        self.message = ""
        self._i = 0
        self._started = False
        self._done = False
        self._escape = False

    def feed(self, delta: str) -> str:
        if not delta or self._done:
            return ""
        self.raw += delta
        if not self._started:
            match = _MESSAGE_JSON_START.search(self.raw)
            if not match:
                return ""
            self._started = True
            self._i = match.end()
        out: list[str] = []
        while self._i < len(self.raw) and not self._done:
            ch = self.raw[self._i]
            self._i += 1
            if self._escape:
                if ch == "n":
                    out.append("\n")
                elif ch == "t":
                    out.append("\t")
                elif ch == "r":
                    out.append("\r")
                elif ch == "u" and self._i + 4 <= len(self.raw):
                    hexpart = self.raw[self._i : self._i + 4]
                    try:
                        out.append(chr(int(hexpart, 16)))
                    except ValueError:
                        out.append("\\u" + hexpart)
                    self._i += 4
                else:
                    out.append(ch)
                self._escape = False
                continue
            if ch == "\\":
                self._escape = True
                continue
            if ch == '"':
                self._done = True
                break
            out.append(ch)
        piece = "".join(out)
        self.message += piece
        return piece


def _chunk_delta_text(chunk: Any) -> str:
    """Extract text from an OpenAI-style chat completion stream chunk."""
    if chunk is None:
        return ""
    # Completed response handed back despite stream=True
    choices = getattr(chunk, "choices", None)
    if isinstance(choices, (list, tuple)) and choices:
        first = choices[0]
        delta = getattr(first, "delta", None)
        if delta is not None:
            return str(getattr(delta, "content", None) or "")
        message = getattr(first, "message", None)
        if message is not None:
            return str(getattr(message, "content", None) or "")
    if isinstance(chunk, dict):
        choices = chunk.get("choices") or []
        if choices:
            first = choices[0] or {}
            delta = first.get("delta") or {}
            if isinstance(delta, dict) and delta.get("content"):
                return str(delta["content"])
            msg = first.get("message") or {}
            if isinstance(msg, dict) and msg.get("content"):
                return str(msg["content"])
    return ""


def _iter_chat_completion_text(stream_or_response: Any) -> Iterator[str]:
    """Yield text pieces from ``call_llm(..., stream=True)`` result."""
    if stream_or_response is None:
        return
    # Completed response object (adapters that ignore stream=True)
    choices = getattr(stream_or_response, "choices", None)
    if choices is not None and not hasattr(stream_or_response, "__next__"):
        first = choices[0] if choices else None
        if first is not None and hasattr(first, "message") and not hasattr(first, "delta"):
            from agent.auxiliary_client import extract_content_or_reasoning

            text = (extract_content_or_reasoning(stream_or_response) or "").strip()
            if text:
                yield text
            return
    try:
        iterator = iter(stream_or_response)
    except TypeError:
        text = _chunk_delta_text(stream_or_response)
        if text:
            yield text
        return
    for chunk in iterator:
        piece = _chunk_delta_text(chunk)
        if piece:
            yield piece


def _prepare_generate_detail(detail: dict[str, Any]) -> dict[str, Any]:
    """Same heuristic card AI prep as ``generate_outreach_message``."""
    if (detail.get("ai") or {}).get("recommendation"):
        return detail
    client = detail.get("client") or {}
    orders = list(detail.get("orders") or [])
    data_thin = bool(detail.get("data_thin"))
    risks = detail.get("risks") or compute_risks(client, orders, data_thin=data_thin)
    return {
        **detail,
        "ai": heuristic_ai(
            client,
            orders,
            vip=bool(client.get("vip")),
            loyalty=client.get("loyalty_points"),
            data_thin=data_thin,
            risks=risks,
        ),
    }


def _generate_user_prompt(
    detail: dict[str, Any],
    *,
    channel: str,
    seller_name: str,
    seller_facts: str,
) -> str:
    facts = _facts_payload(detail)
    payload = {
        "channel": channel,
        "channel_label": _channel_label(channel),
        "seller_name": seller_name,
        "seller_facts": seller_facts,
        "client": facts.get("client"),
        "orders": facts.get("orders"),
        "risks": facts.get("risks"),
        "conversation": _conversation_facts(detail),
        "data_thin": facts.get("data_thin"),
        "ai": {
            "history_profile": (detail.get("ai") or {}).get("history_profile"),
            "occasion_intent": (detail.get("ai") or {}).get("occasion_intent"),
            "recommendation": (detail.get("ai") or {}).get("recommendation"),
            "source": (detail.get("ai") or {}).get("source"),
            "data_thin": (detail.get("ai") or {}).get("data_thin"),
        },
    }
    return (
        "Сгенерируй КРЕАТИВНЫЙ текст личного сообщения клиенту "
        "(живая формулировка, не шаблон).\n"
        f"Канал отправки: {_channel_label(channel)} "
        "(не дублируй название канала в конце сообщения).\n"
        f"Подпись продавца: {seller_name or '(не задана — мягко из цветочного магазина)'}.\n"
        f"Факты о магазине: {seller_facts or '(нет)'}.\n"
        "JSON фактов (единственный источник истины по клиенту):\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def _finalize_streamed_message(
    raw: str,
    extractor: ProgressiveJsonMessage,
    *,
    channel: str,
    mode: str | None,
) -> tuple[str, str]:
    """Return ``(message, grounding_notes)`` after a streamed generate/rewrite."""
    parsed = _parse_outreach_json(raw)
    if parsed:
        return (
            _strip_channel_trailer(parsed["message"], channel),
            parsed.get("grounding_notes") or "",
        )
    if extractor.message.strip():
        return _strip_channel_trailer(extractor.message.strip(), channel), ""
    if mode == "plain" or (raw or "").strip():
        return _strip_channel_trailer((raw or "").strip(), channel), ""
    return "", ""


def _stream_llm_message_events(
    *,
    system: str,
    user: str,
    temperature: float,
    status_text: str,
) -> Iterator[dict[str, Any]]:
    """Yield status/delta events while calling the LLM with ``stream=True``."""
    yield {"type": "status", "text": status_text}
    from agent.auxiliary_client import call_llm

    stream = call_llm(
        task="compression",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=700,
        temperature=temperature,
        timeout=45.0,
        stream=True,
    )
    extractor = ProgressiveJsonMessage()
    parts: list[str] = []
    mode: str | None = None
    for piece in _iter_chat_completion_text(stream):
        parts.append(piece)
        if mode is None:
            stripped = "".join(parts).lstrip()
            if not stripped:
                continue
            mode = "json" if stripped.startswith("{") else "plain"
        if mode == "plain":
            yield {"type": "delta", "text": piece}
        else:
            visible = extractor.feed(piece)
            if visible:
                yield {"type": "delta", "text": visible}
    yield {
        "type": "_raw",
        "raw": "".join(parts).strip(),
        "extractor": extractor,
        "mode": mode,
    }


def iter_generate_outreach_events(
    detail: dict[str, Any],
    *,
    channel: str = "telegram",
    refresh_ai: bool = False,
    seller_name: str = "",
    seller_facts: str = "",
) -> Iterator[dict[str, Any]]:
    """NDJSON events for streaming generate: status → delta* → done|error."""
    channel = (channel or "telegram").strip().lower()
    seller_name, seller_facts = normalize_seller_fields(seller_name, seller_facts)
    _ = refresh_ai
    detail = _prepare_generate_detail(detail)
    fallback = heuristic_outreach_message(
        detail,
        channel=channel,
        seller_name=seller_name,
        seller_facts=seller_facts,
    )
    facts = facts_panel(detail)
    try:
        raw_holder: dict[str, Any] = {}
        for ev in _stream_llm_message_events(
            system=_OUTREACH_SYSTEM(seller_name, seller_facts),
            user=_generate_user_prompt(
                detail,
                channel=channel,
                seller_name=seller_name,
                seller_facts=seller_facts,
            ),
            temperature=OUTREACH_GENERATE_TEMPERATURE,
            status_text="Генерируем текст…",
        ):
            if ev.get("type") == "_raw":
                raw_holder = ev
                continue
            yield ev
        message, notes = _finalize_streamed_message(
            str(raw_holder.get("raw") or ""),
            raw_holder.get("extractor") or ProgressiveJsonMessage(),
            channel=channel,
            mode=raw_holder.get("mode"),
        )
        if not message:
            log.warning("moysklad outreach stream: empty, using heuristic")
            result = _apply_sanity(
                fallback,
                detail,
                channel=channel,
                seller_name=seller_name,
                seller_facts=seller_facts,
            )
            yield {"type": "done", "ok": True, **result}
            return
        result = {
            "message": message,
            "grounding_notes": notes or fallback.get("grounding_notes") or "",
            "source": "llm",
            "channel": channel,
            "facts": facts,
            "ai": detail.get("ai"),
            "seller_name": seller_name,
            "seller_facts": seller_facts,
        }
        result = _apply_sanity(
            result,
            detail,
            channel=channel,
            seller_name=seller_name,
            seller_facts=seller_facts,
        )
        # If sanity rewrote the message, push a final replace delta
        if result.get("message") and result["message"] != message:
            yield {"type": "replace", "text": result["message"]}
        yield {"type": "done", "ok": True, **result}
    except Exception as exc:
        log.warning("moysklad outreach stream unavailable: %s", exc)
        result = _apply_sanity(
            {**fallback, "error": str(exc), "ai": detail.get("ai")},
            detail,
            channel=channel,
            seller_name=seller_name,
            seller_facts=seller_facts,
        )
        yield {"type": "replace", "text": result.get("message") or fallback["message"]}
        yield {"type": "done", "ok": True, **result}


def iter_rewrite_outreach_events(
    draft: str,
    *,
    channel: str = "telegram",
    seller_name: str = "",
    seller_facts: str = "",
    detail: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """NDJSON events for streaming rewrite."""
    channel = (channel or "telegram").strip().lower()
    seller_name, seller_facts = normalize_seller_fields(seller_name, seller_facts)
    draft = (draft or "").strip()
    if not draft:
        yield {
            "type": "done",
            "ok": True,
            "message": "",
            "grounding_notes": "Пустой черновик — нечего переписывать.",
            "source": "empty",
            "channel": channel,
            "seller_name": seller_name,
            "seller_facts": seller_facts,
            "facts": facts_panel(detail) if detail else {},
        }
        return

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

    risks = _risks_from_detail(detail)
    if risks.get("do_not_upsell") and _UPSELL_FLOWER_RE.search(draft):
        fallback_msg = _payment_reminder_message(
            detail or {}, seller_name=seller_name
        )
    else:
        fallback_msg = _heuristic_rewrite(draft)
    facts_block = facts_panel(detail) if detail else {}
    user = (
        "Перепиши черновик ЗАНОВО — продающе и по-человечески "
        "(сильнее ценность и мягкий CTA, без выдуманных акций).\n"
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
                    "risks": {
                        "has_debt": bool(risks.get("has_debt")),
                        "debt_amount": risks.get("debt_amount"),
                        "unpaid_order_count": risks.get("unpaid_order_count"),
                        "do_not_upsell": bool(risks.get("do_not_upsell")),
                        "flags": list(risks.get("flags") or []),
                    },
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
        raw_holder: dict[str, Any] = {}
        for ev in _stream_llm_message_events(
            system=_REWRITE_SYSTEM,
            user=user,
            temperature=OUTREACH_REWRITE_TEMPERATURE,
            status_text="Переписываем…",
        ):
            if ev.get("type") == "_raw":
                raw_holder = ev
                continue
            yield ev
        message, notes = _finalize_streamed_message(
            str(raw_holder.get("raw") or ""),
            raw_holder.get("extractor") or ProgressiveJsonMessage(),
            channel=channel,
            mode=raw_holder.get("mode"),
        )
        if not message:
            result = _apply_sanity(
                {
                    "message": fallback_msg,
                    "grounding_notes": "Heuristic cleanup (LLM parse failed).",
                    "source": "heuristic_rewrite",
                    "channel": channel,
                    "facts": facts_block,
                    "seller_name": seller_name,
                    "seller_facts": seller_facts,
                },
                detail,
                channel=channel,
                seller_name=seller_name,
                seller_facts=seller_facts,
            )
            yield {"type": "replace", "text": result.get("message") or fallback_msg}
            yield {"type": "done", "ok": True, **result}
            return
        result = _apply_sanity(
            {
                "message": message,
                "grounding_notes": notes or "Переписано с сохранением фактов.",
                "source": "llm_rewrite",
                "channel": channel,
                "facts": facts_block,
                "seller_name": seller_name,
                "seller_facts": seller_facts,
                "ai": (detail or {}).get("ai"),
            },
            detail,
            channel=channel,
            seller_name=seller_name,
            seller_facts=seller_facts,
        )
        if result.get("message") and result["message"] != message:
            yield {"type": "replace", "text": result["message"]}
        yield {"type": "done", "ok": True, **result}
    except Exception as exc:
        log.warning("moysklad outreach rewrite stream unavailable: %s", exc)
        result = _apply_sanity(
            {
                "message": fallback_msg,
                "grounding_notes": "Heuristic cleanup (LLM unavailable).",
                "source": "heuristic_rewrite",
                "channel": channel,
                "facts": facts_block,
                "seller_name": seller_name,
                "seller_facts": seller_facts,
                "error": str(exc),
                "ai": (detail or {}).get("ai"),
            },
            detail,
            channel=channel,
            seller_name=seller_name,
            seller_facts=seller_facts,
        )
        yield {"type": "replace", "text": result.get("message") or fallback_msg}
        yield {"type": "done", "ok": True, **result}


def iter_suggest_bouquet_events(
    detail: dict[str, Any],
    *,
    channel: str = "telegram",
    seller_name: str = "",
    seller_facts: str = "",
) -> Iterator[dict[str, Any]]:
    """NDJSON events: suggest concrete historical bouquet."""
    channel = (channel or "telegram").strip().lower()
    seller_name, seller_facts = normalize_seller_fields(seller_name, seller_facts)
    detail = _prepare_generate_detail(detail)
    fallback = heuristic_bouquet_suggestion(
        detail,
        channel=channel,
        seller_name=seller_name,
        seller_facts=seller_facts,
    )
    risks = _risks_from_detail(detail)
    cands = list(fallback.get("bouquet_candidates") or _historical_bouquet_candidates(detail))
    payload = {
        "channel": channel,
        "channel_label": _channel_label(channel),
        "seller_name": seller_name,
        "seller_facts": seller_facts,
        "client": (detail.get("client") or {}),
        "historical_bouquets": cands,
        "orders": (detail.get("orders") or [])[:8],
        "risks": {
            "has_debt": bool(risks.get("has_debt")),
            "do_not_upsell": bool(risks.get("do_not_upsell")),
            "flags": list(risks.get("flags") or []),
        },
        "ai": {
            "recommendation": (detail.get("ai") or {}).get("recommendation"),
            "occasion_intent": (detail.get("ai") or {}).get("occasion_intent"),
        },
    }
    user = (
        "Предложи клиенту КОНКРЕТНЫЙ букет из historical_bouquets "
        "(обязательно назови продукт из списка).\n"
        f"Канал: {_channel_label(channel)}.\n"
        f"Подпись: {seller_name or '(не задана)'}.\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
    try:
        raw_holder: dict[str, Any] = {}
        for ev in _stream_llm_message_events(
            system=_BOUQUET_SYSTEM,
            user=user,
            temperature=OUTREACH_BOUQUET_TEMPERATURE,
            status_text="Подбираем букет из истории…",
        ):
            if ev.get("type") == "_raw":
                raw_holder = ev
                continue
            yield ev
        message, notes = _finalize_streamed_message(
            str(raw_holder.get("raw") or ""),
            raw_holder.get("extractor") or ProgressiveJsonMessage(),
            channel=channel,
            mode=raw_holder.get("mode"),
        )
        use_fallback = not message
        if (
            not use_fallback
            and cands
            and not risks.get("do_not_upsell")
            and not any(str(c["product"]).casefold() in message.casefold() for c in cands)
        ):
            use_fallback = True
        if use_fallback:
            result = _apply_sanity(
                fallback,
                detail,
                channel=channel,
                seller_name=seller_name,
                seller_facts=seller_facts,
            )
            yield {"type": "replace", "text": result.get("message") or fallback["message"]}
            yield {"type": "done", "ok": True, **result}
            return
        result = _apply_sanity(
            {
                "message": message,
                "grounding_notes": notes or fallback.get("grounding_notes") or "",
                "source": "llm_bouquet",
                "channel": channel,
                "facts": facts_panel(detail),
                "seller_name": seller_name,
                "seller_facts": seller_facts,
                "ai": detail.get("ai"),
                "bouquet": fallback.get("bouquet"),
                "bouquet_candidates": cands,
            },
            detail,
            channel=channel,
            seller_name=seller_name,
            seller_facts=seller_facts,
        )
        if result.get("message") and result["message"] != message:
            yield {"type": "replace", "text": result["message"]}
        yield {"type": "done", "ok": True, **result}
    except Exception as exc:
        log.warning("moysklad bouquet stream unavailable: %s", exc)
        result = _apply_sanity(
            {**fallback, "error": str(exc)},
            detail,
            channel=channel,
            seller_name=seller_name,
            seller_facts=seller_facts,
        )
        yield {"type": "replace", "text": result.get("message") or fallback["message"]}
        yield {"type": "done", "ok": True, **result}


def iter_paraphrase_outreach_events(
    draft: str,
    *,
    channel: str = "telegram",
    seller_name: str = "",
    seller_facts: str = "",
    detail: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """NDJSON events for full paraphrase (must differ from draft)."""
    channel = (channel or "telegram").strip().lower()
    seller_name, seller_facts = normalize_seller_fields(seller_name, seller_facts)
    draft = (draft or "").strip()
    facts_block = facts_panel(detail) if detail else {}
    if not draft:
        yield {
            "type": "done",
            "ok": True,
            "message": "",
            "grounding_notes": "Пустой черновик — нечего парафразировать.",
            "source": "empty",
            "channel": channel,
            "seller_name": seller_name,
            "seller_facts": seller_facts,
            "facts": facts_block,
        }
        return
    risks = _risks_from_detail(detail)
    if risks.get("do_not_upsell") and _UPSELL_FLOWER_RE.search(draft):
        fallback_msg = _payment_reminder_message(detail or {}, seller_name=seller_name)
    else:
        fallback_msg = heuristic_paraphrase(draft, channel=channel)
    user = (
        "Сделай ПОЛНУЮ парафразу (не sales-rewrite и не generate с нуля).\n"
        f"Канал: {_channel_label(channel)}.\n"
        f"Подпись: {seller_name or '(не задана)'}.\n"
        f"Факты магазина: {seller_facts or '(нет)'}.\n"
    )
    if detail:
        user += (
            "Факты клиента:\n"
            + json.dumps(
                {
                    "client": (detail.get("client") or {}),
                    "orders": (detail.get("orders") or [])[:5],
                    "risks": {
                        "has_debt": bool(risks.get("has_debt")),
                        "do_not_upsell": bool(risks.get("do_not_upsell")),
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
    user += f"Исходный черновик:\n{draft}"
    try:
        raw_holder: dict[str, Any] = {}
        for ev in _stream_llm_message_events(
            system=_PARAPHRASE_SYSTEM,
            user=user,
            temperature=OUTREACH_PARAPHRASE_TEMPERATURE,
            status_text="Делаем полную парафразу…",
        ):
            if ev.get("type") == "_raw":
                raw_holder = ev
                continue
            yield ev
        message, notes = _finalize_streamed_message(
            str(raw_holder.get("raw") or ""),
            raw_holder.get("extractor") or ProgressiveJsonMessage(),
            channel=channel,
            mode=raw_holder.get("mode"),
        )
        if not message or _too_similar(message, draft):
            message = fallback_msg
            notes = (notes + " Heuristic paraphrase (similarity guard).").strip()
            source = "heuristic_paraphrase"
            yield {"type": "replace", "text": message}
        else:
            source = "llm_paraphrase"
        result = _apply_sanity(
            {
                "message": message,
                "grounding_notes": notes or "Полная парафраза с сохранением фактов.",
                "source": source,
                "channel": channel,
                "facts": facts_block,
                "seller_name": seller_name,
                "seller_facts": seller_facts,
                "ai": (detail or {}).get("ai"),
            },
            detail,
            channel=channel,
            seller_name=seller_name,
            seller_facts=seller_facts,
        )
        if result.get("message") and result["message"] != message:
            yield {"type": "replace", "text": result["message"]}
        yield {"type": "done", "ok": True, **result}
    except Exception as exc:
        log.warning("moysklad paraphrase stream unavailable: %s", exc)
        result = _apply_sanity(
            {
                "message": fallback_msg,
                "grounding_notes": "Heuristic paraphrase (LLM unavailable).",
                "source": "heuristic_paraphrase",
                "channel": channel,
                "facts": facts_block,
                "seller_name": seller_name,
                "seller_facts": seller_facts,
                "error": str(exc),
                "ai": (detail or {}).get("ai"),
            },
            detail,
            channel=channel,
            seller_name=seller_name,
            seller_facts=seller_facts,
        )
        yield {"type": "replace", "text": result.get("message") or fallback_msg}
        yield {"type": "done", "ok": True, **result}


def iter_personalize_batch_events(
    rows: list[dict[str, Any]],
    *,
    channel: str = "telegram",
    seller_name: str = "",
    seller_facts: str = "",
    max_workers: int = 3,
) -> Iterator[dict[str, Any]]:
    """Parallel per-client generate; yield ``client_done`` as each finishes."""
    channel = (channel or "telegram").strip().lower()
    seller_name, seller_facts = normalize_seller_fields(seller_name, seller_facts)
    total = len(rows)
    yield {"type": "batch_start", "total": total, "channel": channel}
    if total == 0:
        yield {"type": "batch_done", "total": 0, "ok_count": 0}
        return

    workers = max(1, min(int(max_workers or 3), 5, total))

    def _one(index: int, row: dict[str, Any]) -> dict[str, Any]:
        client = row.get("client") if isinstance(row.get("client"), dict) else row
        cid = str((client or {}).get("id") or row.get("id") or "")
        cname = str((client or {}).get("name") or row.get("name") or "")
        try:
            out = build_outreach_for_row(
                row,
                channel=channel,
                refresh_ai=True,
                seller_name=seller_name,
                seller_facts=seller_facts,
            )
            return {
                "ok": True,
                "index": index,
                "client_id": out.get("client_id") or cid,
                "client_name": out.get("client_name") or cname,
                "message": out.get("message") or "",
                "grounding_notes": out.get("grounding_notes") or "",
                "source": out.get("source") or "",
                "error": out.get("error"),
            }
        except Exception as exc:  # pragma: no cover
            return {
                "ok": False,
                "index": index,
                "client_id": cid,
                "client_name": cname,
                "message": "",
                "error": str(exc),
            }

    ok_count = 0
    done_count = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_one, i, row): i for i, row in enumerate(rows)
        }
        for fut in as_completed(futures):
            payload = fut.result()
            done_count += 1
            if payload.get("ok") and payload.get("message"):
                ok_count += 1
            yield {
                "type": "client_done",
                "done": done_count,
                "total": total,
                **payload,
            }
    yield {
        "type": "batch_done",
        "total": total,
        "ok_count": ok_count,
    }


def iter_generate_outreach_for_row_events(
    row: dict[str, Any],
    *,
    channel: str = "telegram",
    refresh_ai: bool = True,
    seller_name: str = "",
    seller_facts: str = "",
) -> Iterator[dict[str, Any]]:
    """Stream generate for a catalog row; stamp client id/name on done."""
    detail = build_client_detail(row)
    client_id = (detail.get("client") or {}).get("id")
    client_name = (detail.get("client") or {}).get("name")
    for ev in iter_generate_outreach_events(
        detail,
        channel=channel,
        refresh_ai=refresh_ai,
        seller_name=seller_name,
        seller_facts=seller_facts,
    ):
        if ev.get("type") == "done":
            ev = {
                **ev,
                "detail_ok": True,
                "client_id": client_id,
                "client_name": client_name,
            }
        yield ev
