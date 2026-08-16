"""AI outreach draft text for MoySklad campaigns (Telegram / WhatsApp).

Writes like a free chat with a salesperson — warm, creative, varied —
while staying grounded on client-card facts. Never invents discounts,
orders, phones, VIP, or debt. Seller identity from shop settings.
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
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


def _days_ago_label(days: int) -> str:
    if days <= 1:
        return "сегодня-вчера"
    if days < 7:
        return f"{days} дн. назад"
    if days < 30:
        return f"{days // 7} нед. назад"
    if days < 365:
        return f"~{max(1, round(days / 30))} мес. назад"
    return f"больше {days // 365} г. назад"


def _time_grounding(detail: dict[str, Any]) -> dict[str, Any]:
    """Anchor «now» for the LLM.

    Raw order dates alone let the model invent recency — a bouquet delivered
    two months ago gets described as «до сих пор радует». Give it today's
    date and the elapsed time explicitly.
    """
    today = datetime.now(timezone.utc).date()
    out: dict[str, Any] = {"today": today.isoformat()}
    latest: Optional[date] = None
    for order in detail.get("orders") or []:
        raw = str((order or {}).get("date") or "")[:10]
        try:
            parsed = date.fromisoformat(raw)
        except ValueError:
            continue
        if latest is None or parsed > latest:
            latest = parsed
    if latest is not None:
        days = max(0, (today - latest).days)
        out["last_order_date"] = latest.isoformat()
        out["days_since_last_order"] = days
        out["last_order_ago"] = _days_ago_label(days)
    return out


def _card_ai_for_prompt(detail: dict[str, Any]) -> dict[str, Any]:
    """Card AI block for the outreach prompt: cached DeepSeek summary when
    fresh (the «good» card text), heuristic otherwise — never an extra LLM
    call on the generate path."""
    client = detail.get("client") or {}
    cid = str(client.get("id") or "").strip()
    if cid:
        try:
            from plugins.moysklad.client_ai_cache import (
                facts_fingerprint,
                get_client_ai,
            )

            cached = get_client_ai(cid, fingerprint=facts_fingerprint(detail))
            if cached and str(cached.get("recommendation") or "").strip():
                return cached
        except Exception:
            log.debug("outreach: client_ai cache read failed", exc_info=True)
    orders = list(detail.get("orders") or [])
    data_thin = bool(detail.get("data_thin"))
    risks = detail.get("risks") or compute_risks(client, orders, data_thin=data_thin)
    return heuristic_ai(
        client,
        orders,
        vip=bool(client.get("vip")),
        loyalty=client.get("loyalty_points"),
        data_thin=data_thin,
        risks=risks,
    )


def _OUTREACH_SYSTEM(seller_name: str, seller_facts: str) -> str:
    sig = (seller_name or "").strip() or _DEFAULT_SELLER_NAME
    facts = (seller_facts or "").strip()
    facts_block = (
        f"О магазине (можно мягко обыграть, не расширяй сверх этого):\n{facts}\n"
        if facts
        else "Факты о магазине не заданы — не придумывай адрес, акции, спецпредложения.\n"
    )
    try:
        from plugins.moysklad.outreach_skills import prompt_examples_block

        skills_block = prompt_examples_block()
    except Exception:
        skills_block = ""
    if skills_block:
        facts_block = facts_block + skills_block
    return f"""Ты — продавец цветочного магазина в переписке с клиентом (WhatsApp/Telegram).
Пиши СВОБОДНО, как в живом чате с хорошим вкусом: тепло, по-человечески,
с лёгким креативом и разными формулировками каждый раз. Не канцелярит,
не «робот-CRM», не один и тот же скелет «Здравствуйте… Это… Последний заказ…».
Отвечай на русском.

Подпись / имя продавца: {sig}
{facts_block}
Опора на данные клиента (мягкий якорь, не смирительная рубашка):
• Имя, заказы, букеты/составы, даты, суммы, теги, поводы, риски — только из JSON.
  Не выдумывай скидки, промокоды, акции, телефоны, VIP, баллы, адреса, долг.
• Состав заказа — поле orders[].composition / orders[].line_items (позиции номенклатуры
  из МойСклад). Если состав есть — обязательно опирайся на него в рекомендации
  (назови цветы/букет словами из состава). Не подменяй состав кодом заказа.
• Конкретные букеты/SKU называй, если они есть в истории заказов; иначе — мягко
  и общо, без фейкового наличия на складе.
• Даты — по-человечески («в середине мая»), не сырой ISO и не внутренние коды
  вроде «1605-02», если это не название букета.
• Время: time.today — сегодняшняя дата, time.days_since_last_order — сколько дней
  прошло с последнего заказа. Сверяйся с этим: если прошли недели или месяцы,
  НЕ пиши, будто заказ был «на днях», букет «ещё свежий» или «до сих пор радует» —
  говори честно («пару месяцев назад», «этим летом») либо просто предлагай новое.
• Баллы лояльности (client.loyalty_points): если есть и > 0 — мягко напомни,
  что у клиента накоплено N баллов и их можно потратить на следующий заказ.
  Курс/скидку не выдумывай; при 0 или пустом поле — не упоминай баллы.
• Представься через подпись выше (не хардкодь «Это Iris», если подпись другая).
• Канал уже известен — не дописывай в конец «(WhatsApp)» / «(Telegram)».
• Не пиши мета-фразы вроде «без навязанных скидок», «только по вашей истории».
• Если risks.do_not_upsell / has_debt / unpaid — сначала сверка оплаты, без upsell
  букетов/подарков/скидок. Долг не выдумывай.
• Длина: примерно 2–6 коротких предложений; можно чуть живее и образнее.

Ответ — строго JSON без markdown:
{{"message": "текст сообщения клиенту", "grounding_notes": "1-2 предложения: на каких фактах основан текст"}}
"""


_REWRITE_SYSTEM = """Ты — редактор исходящих сообщений цветочного магазина.
Перепиши черновик ПРОДАЮЩЕ и по-человечески — свободно, как живой чат:
ясная ценность, тёплый тон, мягкий призыв ответить, без канцелярита.
Отвечай на русском. Перепиши ЗАНОВО (не косметика пары слов), сохрани факты.

Опора на данные:
• Не выдумывай скидки, промокоды, акции, заказы, даты, суммы, VIP, адреса, долг.
• Сохрани смысл и реальные факты исходника; усили продающую подачу и живость.
• Убери мета-фразы («без навязанных скидок»), сырые ISO-даты, коды заказов,
  ярлыки канала в скобках.
• Подпись/факты продавца из user prompt — если есть.
• При долге / do_not_upsell — убери upsell, переориентируй на сверку оплаты.
• 2–6 коротких предложений, живой тон.

Ответ — строго JSON без markdown:
{"message": "новый текст", "grounding_notes": "что сохранили / что усилили в продаже"}
"""


_SANITY_SYSTEM = """Ты — лёгкий контролёр смысла. НЕ переписывай текст ради стиля,
«лучшей продажи» или вкуса — чат уже пишет свободно. Трогай только явные сбои.

Проверь ТОЛЬКО:
1. Долг / неоплата / do_not_upsell + в тексте upsell букетов/подарков/скидок —
   тогда ok=false и revised_text про сверку оплаты (долг не выдумывай).
2. Явно выдуманные факты (скидка/VIP/телефон/заказ), которых нет в JSON.
3. Иначе ok=true, issues=[], revised_text=null — оставь креатив как есть.

Ответ — строго JSON без markdown:
{"ok": true, "issues": [], "revised_text": null}
"""


_BOUQUET_SYSTEM = """Ты — флорист в живом чате с клиентом.
Предложи КОНКРЕТНЫЙ букет из ЕГО истории заказов —
смотри orders[].composition / orders[].line_items / product_snippet в JSON —
тепло, свободно, по-человечески, без канцелярита. Отвечай на русском.

Опора на данные:
• Только факты из JSON orders/client/risks + подпись продавца.
  Не выдумывай названия букетов, скидки, акции, наличие на складе.
• Обязательно назови исторический букет/состав словами из composition
  (или line_items / product_snippet, если composition пуст; падеж можно смягчить).
• Мягко предложи повторить / собрать похожий; обращение по имени; 2–6 предложений.
• Не хардкодь «Это Iris», если подпись другая; без ярлыка канала в конце.
• При do_not_upsell / has_debt — НЕ предлагай букет, только сверка оплаты.

Ответ — строго JSON без markdown:
{"message": "текст", "grounding_notes": "какой сниппет/заказ взят"}
"""


_PARAPHRASE_SYSTEM = """Ты — лингвист-редактор. Сделай ПОЛНУЮ ПАРАФРАЗУ черновика.
Это НЕ генерация с нуля по карточке и НЕ «продающий» рерайт с новым CTA.

Тот же смысл и факты — другой текст: другие слова, структура, порядок мыслей.
Пиши свободно и естественно, как живая переписка. Читатель должен сразу видеть,
что формулировки сменились. Отвечай на русском.

Опора на данные:
• Сохрани факты (имя, даты, суммы, букеты, долг) — ничего не выдумывай.
• Замени почти каждую фразу; поменяй порядок, где уместно.
• Не усиливай продажи и не добавляй новый CTA ради продаж.
• 2–6 предложений. Без ярлыка канала. Без markdown.
• При долге / do_not_upsell — не предлагай букеты; оставь сверку оплаты.

Ответ — строго JSON без markdown:
{"message": "полностью перефразированный текст", "grounding_notes": "что сохранили, как сменили форму"}
"""

# Creative temperatures — closer to free chat, still grounded via prompts.
OUTREACH_GENERATE_TEMPERATURE = 0.8
OUTREACH_REWRITE_TEMPERATURE = 1.0
OUTREACH_BOUQUET_TEMPERATURE = 0.85
OUTREACH_PARAPHRASE_TEMPERATURE = 0.95
OUTREACH_SANITY_TEMPERATURE = 0.1

# Dedicated aux task (reasoning none). Do NOT use ``compression`` — Iris pins
# that to reasoning_effort=medium for card summaries, which stalls TTFT here.
# Used by every campaign button: generate / bouquet / rewrite / paraphrase / sanity.
OUTREACH_LLM_TASK = "moysklad_outreach"
OUTREACH_LLM_TIMEOUT = 30.0
OUTREACH_LLM_MAX_TOKENS = 700
# Belt-and-suspenders when plugin defaults / volume config lag behind.
_OUTREACH_NO_REASONING = {"enabled": False, "effort": "none"}
_OUTREACH_EXTRA_BODY = {"reasoning": {"enabled": False}}

_PLAIN_STREAM_TAIL = (
    "\n\nSTREAM MODE: отвечай ТОЛЬКО текстом сообщения клиенту. "
    "Пиши свободно, как в чате. Без JSON, без markdown, без grounding_notes, "
    "без кавычек-обёртки."
)


def _as_plain_stream_system(system: str) -> str:
    """Drop JSON-only rule so the model can emit visible tokens immediately."""
    text = (system or "").rstrip()
    # Strip trailing "Ответ — строго JSON..." (numbered or bare).
    cut = re.search(
        r"\n(?:\d+\.\s+)?Ответ\s*[—\-]\s*строго JSON[\s\S]*$",
        text,
        re.IGNORECASE,
    )
    if cut:
        text = text[: cut.start()].rstrip()
    return text + _PLAIN_STREAM_TAIL


def _compact_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))

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
                "composition": o.get("composition") or None,
                "line_items": list(o.get("line_items") or [])[:8],
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
    if risks.get("failed_customer"):
        message = (
            f"{greeting} {intro}{fact_hint} "
            f"Если снова планируете букет — напишите, подберём спокойно под ваш повод. "
            f"Про прошлые неоплаченные заказы не спрашиваем."
        )
        notes = "Несостоявшийся клиент — без chase оплаты и без ссылок на сорвавшийся заказ."
    elif risks.get("do_not_upsell"):
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
        # Chat mode returns an operator-facing reply alongside the draft.
        "reply": str(data.get("reply") or "").strip(),
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
        "Лёгкая проверка смысла: трогай ТОЛЬКО долг+upsell или явные выдуманные факты. "
        "Стиль/креатив не переписывай.\n"
        f"Канал: {_channel_label(channel)}.\n"
        f"Подпись продавца: {seller_name or '(не задана)'}.\n"
        f"Факты о магазине: {seller_facts or '(нет)'}.\n"
        "Риски (якорь по долгу):\n"
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
            task=OUTREACH_LLM_TASK,
            messages=[
                {"role": "system", "content": _SANITY_SYSTEM},
                {"role": "user", "content": user},
            ],
            max_tokens=OUTREACH_LLM_MAX_TOKENS,
            temperature=OUTREACH_SANITY_TEMPERATURE,
            timeout=OUTREACH_LLM_TIMEOUT,
            reasoning_config=_OUTREACH_NO_REASONING,
            extra_body=_OUTREACH_EXTRA_BODY,
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
    if refresh_ai:
        # Force a fresh DeepSeek card summary when caller asks to renew AI.
        from plugins.moysklad.client_card import generate_ai_for_detail

        detail = {**detail, "ai": generate_ai_for_detail(detail)}
    elif str((detail.get("ai") or {}).get("source") or "") != "llm":
        # Cached DeepSeek card summary when fresh, heuristic otherwise —
        # full LLM card regen stays a separate «Обновить AI» action.
        detail = {**detail, "ai": _card_ai_for_prompt(detail)}

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
        "time": _time_grounding(detail),
        "ai": {
            "history_profile": (detail.get("ai") or {}).get("history_profile"),
            "occasion_intent": (detail.get("ai") or {}).get("occasion_intent"),
            "recommendation": (detail.get("ai") or {}).get("recommendation"),
            "source": (detail.get("ai") or {}).get("source"),
            "data_thin": (detail.get("ai") or {}).get("data_thin"),
        },
    }
    user = (
        "Напиши личное сообщение клиенту СВОБОДНО, как в живом чате продавца — "
        "креативно и по-человечески, опираясь на факты JSON (не шаблон CRM).\n"
        f"Сегодня: {datetime.now(timezone.utc).date().isoformat()} — сверяй все "
        "формулировки о времени с этой датой и time.days_since_last_order.\n"
        f"Канал отправки: {_channel_label(channel)} "
        "(не дублируй название канала в конце сообщения).\n"
        f"Подпись продавца: {seller_name or '(не задана — мягко из цветочного магазина)'}.\n"
        f"Факты о магазине: {seller_facts or '(нет)'}.\n"
        "JSON фактов клиента (якорь — не выдумывай сверх них):\n"
        + _compact_json(payload)
    )
    try:
        from agent.auxiliary_client import call_llm, extract_content_or_reasoning

        response = call_llm(
            task=OUTREACH_LLM_TASK,
            messages=[
                {
                    "role": "system",
                    "content": _OUTREACH_SYSTEM(seller_name, seller_facts),
                },
                {"role": "user", "content": user},
            ],
            max_tokens=OUTREACH_LLM_MAX_TOKENS,
            temperature=OUTREACH_GENERATE_TEMPERATURE,
            timeout=OUTREACH_LLM_TIMEOUT,
            reasoning_config=_OUTREACH_NO_REASONING,
            extra_body=_OUTREACH_EXTRA_BODY,
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
        "Перепиши черновик ЗАНОВО — продающе, свободно и по-человечески, "
        "как в живом чате (сильнее ценность и мягкий CTA, без выдуманных акций).\n"
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
            task=OUTREACH_LLM_TASK,
            messages=[
                {"role": "system", "content": _REWRITE_SYSTEM},
                {"role": "user", "content": user},
            ],
            max_tokens=OUTREACH_LLM_MAX_TOKENS,
            temperature=OUTREACH_REWRITE_TEMPERATURE,
            timeout=OUTREACH_LLM_TIMEOUT,
            reasoning_config=_OUTREACH_NO_REASONING,
            extra_body=_OUTREACH_EXTRA_BODY,
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
    """Concrete bouquets from order history (composition / natural snippets)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for order in list(detail.get("orders") or []):
        if not isinstance(order, dict):
            continue
        raw = (
            order.get("composition")
            or (
                "; ".join(str(x) for x in order.get("line_items") or [] if str(x).strip())
                if order.get("line_items")
                else ""
            )
            or order.get("product_snippet")
            or order.get("name")
            or ""
        )
        snip = _natural_product_bit(raw)
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
        "Предложи клиенту КОНКРЕТНЫЙ букет из historical_bouquets — "
        "свободно, как в чате флориста (обязательно назови продукт из списка).\n"
        f"Канал: {_channel_label(channel)}.\n"
        f"Подпись: {seller_name or '(не задана)'}.\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
    try:
        from agent.auxiliary_client import call_llm, extract_content_or_reasoning

        response = call_llm(
            task=OUTREACH_LLM_TASK,
            messages=[
                {"role": "system", "content": _BOUQUET_SYSTEM},
                {"role": "user", "content": user},
            ],
            max_tokens=OUTREACH_LLM_MAX_TOKENS,
            temperature=OUTREACH_BOUQUET_TEMPERATURE,
            timeout=OUTREACH_LLM_TIMEOUT,
            reasoning_config=_OUTREACH_NO_REASONING,
            extra_body=_OUTREACH_EXTRA_BODY,
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
        "Сделай ПОЛНУЮ парафразу — свободно и естественно, как живая переписка "
        "(не sales-rewrite и не generate с нуля).\n"
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
            task=OUTREACH_LLM_TASK,
            messages=[
                {"role": "system", "content": _PARAPHRASE_SYSTEM},
                {"role": "user", "content": user},
            ],
            max_tokens=OUTREACH_LLM_MAX_TOKENS,
            temperature=OUTREACH_PARAPHRASE_TEMPERATURE,
            timeout=OUTREACH_LLM_TIMEOUT,
            reasoning_config=_OUTREACH_NO_REASONING,
            extra_body=_OUTREACH_EXTRA_BODY,
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
                task=OUTREACH_LLM_TASK,
                messages=[
                    {"role": "system", "content": _PARAPHRASE_SYSTEM},
                    {
                        "role": "user",
                        "content": user
                        + "\n\nПРЕДЫДУЩИЙ ОТВЕТ СЛИШКОМ ПОХОЖ. "
                        "Перепиши ещё раз — другие слова и другой порядок фраз.",
                    },
                ],
                max_tokens=OUTREACH_LLM_MAX_TOKENS,
                temperature=min(1.0, OUTREACH_PARAPHRASE_TEMPERATURE + 0.05),
                timeout=OUTREACH_LLM_TIMEOUT,
                reasoning_config=_OUTREACH_NO_REASONING,
                extra_body=_OUTREACH_EXTRA_BODY,
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
    use_draft_cache: bool = True,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Catalog row → detail → outreach draft payload.

    When ``use_draft_cache`` and not ``force_refresh``, serves Redis/file draft
    if present (avoids re-LLM on personalize / re-open). Fresh LLM results are
    written back to the same cache.
    """
    from plugins.moysklad.client_ai_cache import facts_fingerprint
    from plugins.moysklad.outreach_cache import get_outreach_draft, set_outreach_draft

    detail = build_client_detail(row)
    client = detail.get("client") or {}
    cid = str(client.get("id") or "").strip()
    cname = str(client.get("name") or "").strip()
    channel = (channel or "telegram").strip().lower() or "telegram"
    fp = facts_fingerprint(detail)

    if use_draft_cache and not force_refresh and cid:
        cached = get_outreach_draft(cid, channel, facts_fingerprint=fp)
        msg = str((cached or {}).get("message") or "").strip()
        if cached and msg:
            panel = cached.get("facts") if isinstance(cached.get("facts"), dict) else None
            return {
                "message": msg,
                "grounding_notes": str(cached.get("grounding_notes") or ""),
                "source": str(cached.get("source") or "redis-cache"),
                "from_cache": True,
                "cached": True,
                "facts": panel or facts_panel(detail),
                "sanity": cached.get("sanity")
                if isinstance(cached.get("sanity"), dict)
                else None,
                "seller_name": seller_name,
                "seller_facts": seller_facts,
                "channel": channel,
                "detail_ok": True,
                "client_id": cid,
                "client_name": cname or str(cached.get("client_name") or ""),
            }

    result = generate_outreach_message(
        detail,
        channel=channel,
        refresh_ai=refresh_ai or force_refresh,
        seller_name=seller_name,
        seller_facts=seller_facts,
    )
    result["detail_ok"] = True
    result["client_id"] = cid
    result["client_name"] = cname
    result["from_cache"] = False

    if use_draft_cache and cid and str(result.get("message") or "").strip():
        try:
            set_outreach_draft(
                cid,
                channel,
                {
                    "message": result.get("message") or "",
                    "grounding_notes": result.get("grounding_notes") or "",
                    "source": result.get("source") or "",
                    "status": "AI черновик (кэш Redis/файл)",
                    "client_name": cname,
                    "title": f"Черновик · {cname}" if cname else "",
                    "facts": result.get("facts")
                    if isinstance(result.get("facts"), dict)
                    else {},
                    "sanity": result.get("sanity")
                    if isinstance(result.get("sanity"), dict)
                    else None,
                    "facts_fingerprint": fp,
                },
            )
            result["cached"] = True
        except Exception as exc:  # pragma: no cover
            log.warning("moysklad outreach cache write failed: %s", exc)
            result["cached"] = False
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
    """Same card AI prep as ``generate_outreach_message`` (cache → heuristic)."""
    ai = detail.get("ai") or {}
    if ai.get("recommendation") and str(ai.get("source") or "") == "llm":
        return detail
    return {**detail, "ai": _card_ai_for_prompt(detail)}


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
        "time": _time_grounding(detail),
        "ai": {
            "history_profile": (detail.get("ai") or {}).get("history_profile"),
            "occasion_intent": (detail.get("ai") or {}).get("occasion_intent"),
            "recommendation": (detail.get("ai") or {}).get("recommendation"),
            "source": (detail.get("ai") or {}).get("source"),
            "data_thin": (detail.get("ai") or {}).get("data_thin"),
        },
    }
    return (
        "Напиши личное сообщение клиенту СВОБОДНО, как в живом чате продавца — "
        "креативно и по-человечески, опираясь на факты JSON (не шаблон CRM).\n"
        f"Сегодня: {datetime.now(timezone.utc).date().isoformat()} — сверяй все "
        "формулировки о времени с этой датой и time.days_since_last_order.\n"
        f"Канал отправки: {_channel_label(channel)} "
        "(не дублируй название канала в конце сообщения).\n"
        f"Подпись продавца: {seller_name or '(не задана — мягко из цветочного магазина)'}.\n"
        f"Факты о магазине: {seller_facts or '(нет)'}.\n"
        "JSON фактов клиента (якорь — не выдумывай сверх них):\n"
        + _compact_json(payload)
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
    """Yield status/delta events while calling the LLM with ``stream=True``.

    Uses plain-text stream mode so the UI shows tokens immediately (chat-like).
    JSON responses still work via ProgressiveJsonMessage as a fallback.
    """
    yield {"type": "status", "text": status_text}
    from agent.auxiliary_client import call_llm

    stream = call_llm(
        task=OUTREACH_LLM_TASK,
        messages=[
            {"role": "system", "content": _as_plain_stream_system(system)},
            {
                "role": "user",
                "content": user.rstrip()
                + "\n\nОтветь ТОЛЬКО текстом сообщения клиенту (без JSON).",
            },
        ],
        max_tokens=OUTREACH_LLM_MAX_TOKENS,
        temperature=temperature,
        timeout=OUTREACH_LLM_TIMEOUT,
        reasoning_config=_OUTREACH_NO_REASONING,
        extra_body=_OUTREACH_EXTRA_BODY,
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
        "Перепиши черновик ЗАНОВО — продающе, свободно и по-человечески, "
        "как в живом чате (сильнее ценность и мягкий CTA, без выдуманных акций).\n"
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
        "Предложи клиенту КОНКРЕТНЫЙ букет из historical_bouquets — "
        "свободно, как в чате флориста (обязательно назови продукт из списка).\n"
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
        "Сделай ПОЛНУЮ парафразу — свободно и естественно, как живая переписка "
        "(не sales-rewrite и не generate с нуля).\n"
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

    workers = max(1, min(int(max_workers or 6), 8, total))

    def _one(index: int, row: dict[str, Any]) -> dict[str, Any]:
        client = row.get("client") if isinstance(row.get("client"), dict) else row
        cid = str((client or {}).get("id") or row.get("id") or "")
        cname = str((client or {}).get("name") or row.get("name") or "")
        try:
            # Prefer Redis/file draft cache — do not re-LLM every batch run.
            out = build_outreach_for_row(
                row,
                channel=channel,
                refresh_ai=False,
                seller_name=seller_name,
                seller_facts=seller_facts,
                use_draft_cache=True,
                force_refresh=False,
            )
            return {
                "ok": True,
                "index": index,
                "client_id": out.get("client_id") or cid,
                "client_name": out.get("client_name") or cname,
                "message": out.get("message") or "",
                "grounding_notes": out.get("grounding_notes") or "",
                "source": out.get("source") or "",
                "from_cache": bool(out.get("from_cache")),
                "cached": bool(out.get("cached") or out.get("from_cache")),
                "error": out.get("error"),
            }
        except Exception as exc:  # pragma: no cover
            return {
                "ok": False,
                "index": index,
                "client_id": cid,
                "client_name": cname,
                "message": "",
                "from_cache": False,
                "error": str(exc),
            }

    ok_count = 0
    cache_hits = 0
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
            if payload.get("from_cache"):
                cache_hits += 1
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
        "cache_hits": cache_hits,
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


_CHAT_SYSTEM_TAIL = """
Оператор дорабатывает черновик исходящего сообщения в чате с тобой.
Понимай просьбы свободно («короче», «теплее», «добавь про баллы», «убери эмодзи»).
Каждый твой ответ — строго JSON без markdown:
{"reply": "1-2 предложения оператору: что поменял или что предлагаешь",
 "message": "полный обновлённый текст сообщения клиенту"}
Если оператор просто спрашивает совета и текст менять не надо — верни
текущий черновик в message без изменений.
"""


def chat_refine_message(
    detail: dict[str, Any],
    *,
    channel: str = "telegram",
    draft: str = "",
    chat: list[dict[str, str]] | None = None,
    provider: str = "",
    model: str = "",
    seller_name: str = "",
    seller_facts: str = "",
) -> dict[str, Any]:
    """One chat turn over the current draft. Returns ``{reply, message}``.

    The chat carries the same fact anchor as generate (client JSON, time
    grounding, saved навык examples in the system prompt), so refinements
    stay grounded while the operator steers style in natural language.
    """
    channel = (channel or "telegram").strip().lower()
    seller_name, seller_facts = normalize_seller_fields(seller_name, seller_facts)
    detail = _prepare_generate_detail(detail)
    facts_prompt = _generate_user_prompt(
        detail,
        channel=channel,
        seller_name=seller_name,
        seller_facts=seller_facts,
    )
    draft_text = (draft or "").strip()
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": _OUTREACH_SYSTEM(seller_name, seller_facts)
            + _CHAT_SYSTEM_TAIL,
        },
        {
            "role": "user",
            "content": facts_prompt
            + "\n\nТекущий черновик сообщения:\n"
            + (draft_text or "(пусто — предложи первый вариант)"),
        },
    ]
    for turn in (chat or [])[-12:]:
        role = str((turn or {}).get("role") or "").strip().lower()
        content = str((turn or {}).get("content") or "").strip()
        if not content or role not in ("user", "assistant"):
            continue
        messages.append({"role": role, "content": content[:2000]})

    from agent.auxiliary_client import call_llm, extract_content_or_reasoning

    kwargs: dict[str, Any] = {
        "task": OUTREACH_LLM_TASK,
        "messages": messages,
        "max_tokens": OUTREACH_LLM_MAX_TOKENS,
        "temperature": 0.7,
        "timeout": OUTREACH_LLM_TIMEOUT,
        "reasoning_config": _OUTREACH_NO_REASONING,
        "extra_body": _OUTREACH_EXTRA_BODY,
    }
    if (provider or "").strip():
        kwargs["provider"] = provider.strip()
    if (model or "").strip():
        kwargs["model"] = model.strip()
    response = call_llm(**kwargs)
    text = (extract_content_or_reasoning(response) or "").strip()
    parsed = _parse_outreach_json(text)
    if parsed and parsed.get("message"):
        reply = str(
            parsed.get("reply") or parsed.get("grounding_notes") or "Обновил текст."
        ).strip()
        return {
            "ok": True,
            "reply": reply,
            "message": _strip_channel_trailer(str(parsed["message"]), channel),
        }
    # Not JSON — treat the whole answer as advice, keep the draft as is.
    if text:
        return {"ok": True, "reply": text[:1500], "message": draft_text}
    return {
        "ok": False,
        "error": "empty_llm_response",
        "reply": "",
        "message": draft_text,
    }
