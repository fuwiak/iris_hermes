"""Client detail card: orders, stats, messaging links, AI summary/recommendation.

AI path uses auxiliary ``call_llm`` with a strict system prompt. When the LLM
is unavailable or data is thin, a deterministic heuristic summary is returned
so the UI never invents contacts, VIP status, or orders.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from plugins.moysklad.classify import _public_client

log = logging.getLogger(__name__)

_PHONE_DIGITS_RE = re.compile(r"\D+")
_EVENT_TAG_RE = re.compile(
    r"(событие|март|валентин|учитель|учительниц|мамин|новый.?год|"
    r"свадьб|годовщин|1\s*сентябр|день\s*знан|8\s*марта|14\s*феврал)",
    re.IGNORECASE,
)
_VIP_RE = re.compile(r"\b(vip|вип)\b", re.IGNORECASE)
_LOYALTY_RE = re.compile(r"(постоянн|loyal|лояльн)", re.IGNORECASE)

_RU_OCCASIONS = (
    "8 Марта",
    "День святого Валентина (14 февраля)",
    "День матери (РФ)",
    "День учителя",
    "1 сентября / День знаний",
    "Новый год",
    "годовщина свадьбы (только если есть в данных)",
)

_AI_SYSTEM = """Ты — помощник продавца цветочного магазина (квіти/цветы), B2B и розница.
Отвечай строго на русском.

ЖЁСТКИЕ ПРАВИЛА (нарушать нельзя):
1. Используй ТОЛЬКО факты из JSON клиента и заказов. Ничего не выдумывай.
2. Нельзя придумывать телефон, email, Telegram, VIP, баллы лояльности, каналы,
   заказы, даты, суммы, адреса — если поля нет или пусто, так и скажи.
3. Цитируй реальные даты и суммы заказов из JSON (хотя бы 1–2 примера).
4. Если данных мало (мало заказов / нет контактов) — явно напиши «данных мало».
5. Рекомендации: когда связаться (~5 дней до ожидаемого повода/доставки),
   что предложить, ориентир чека — только из среднего чека / истории.
6. Праздники/поводы упоминай только если факты (месяцы заказов, теги, описание)
   это поддерживают. Известные поводы: """ + ", ".join(_RU_OCCASIONS) + """.
7. Не предлагай каналы связи, которых нет в JSON.
8. Ответ — строго JSON без markdown:
{
  "history_profile": "2-5 предложений: история и профиль",
  "occasion_intent": "2-5 предложений: повод/intent, сезонность, окна касания",
  "recommendation": "2-6 предложений: что и когда предложить продавцу"
}
"""


def _digits_phone(phone: str) -> str:
    digits = _PHONE_DIGITS_RE.sub("", phone or "")
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    return digits


def messaging_links(
    *,
    phone: str = "",
    tg_nick: str = "",
    tg_conversation: str = "",
) -> dict[str, Any]:
    """Deep-links / hints for WhatsApp + Telegram (Green API / Hermes skills)."""
    digits = _digits_phone(phone)
    wa_url = f"https://wa.me/{digits}" if digits else ""
    nick = (tg_nick or "").strip()
    if nick.startswith("@"):
        nick = nick[1:]
    tg_url = ""
    conv = (tg_conversation or "").strip()
    if conv.startswith("http://") or conv.startswith("https://") or conv.startswith("tg:"):
        tg_url = conv
    elif nick:
        tg_url = f"https://t.me/{nick}"

    primary = ""
    if digits:
        primary = "WhatsApp"
    elif tg_url or nick:
        primary = "Telegram"

    return {
        "phone_digits": digits,
        "whatsapp_url": wa_url,
        "telegram_url": tg_url,
        "tg_nick": (tg_nick or "").strip(),
        "primary_channel": primary,
        "hint": (
            "Чат и отправка — через WhatsApp / Telegram "
            "(Green API, если настроен, или Hermes skills / send_message). "
            "Кнопки открывают deep-link; массовая рассылка — раздел «Рассылки»."
        ),
        "hermes_hint": (
            "В агенте: skill WhatsApp/Telegram или tool send_message "
            f"с телефоном {digits or '—'} / tg {('@' + nick) if nick else '—'}."
        ),
    }


def _order_public(item: dict[str, Any]) -> dict[str, Any]:
    moment = str(item.get("moment") or item.get("Дата") or "").strip()
    amount = item.get("sum")
    if amount is None:
        amount = item.get("Сумма")
    try:
        amount_f = float(amount or 0)
    except (TypeError, ValueError):
        amount_f = 0.0
    channel = str(item.get("channel") or item.get("Канал продаж") or "").strip()
    name = str(item.get("name") or "").strip()
    desc = str(item.get("description") or "").strip()
    snippet = str(item.get("product_snippet") or "").strip()
    if not snippet:
        snippet = (desc or name)[:120]
    return {
        "id": str(item.get("id") or "").strip(),
        "name": name,
        "date": moment,
        "sum": round(amount_f, 2),
        "channel": channel,
        "product_snippet": snippet,
        "description": desc,
    }


def _split_tags(tags: list[Any]) -> dict[str, list[str]]:
    marketplace: list[str] = []
    loyalty: list[str] = []
    events: list[str] = []
    other: list[str] = []
    for raw in tags or []:
        t = str(raw or "").strip()
        if not t:
            continue
        low = t.lower().replace("ё", "е")
        if any(x in low for x in ("flow", "wow", "маркет", "marketplace", "flw")):
            marketplace.append(t)
        elif _VIP_RE.search(t) or _LOYALTY_RE.search(t):
            loyalty.append(t)
        elif _EVENT_TAG_RE.search(t) or "событие" in low:
            events.append(t)
        else:
            other.append(t)
    return {
        "marketplace": marketplace,
        "loyalty": loyalty,
        "events": events,
        "other": other,
    }


def _is_vip(tags: list[str], state: str) -> bool:
    blob = " ".join([*(tags or []), state or ""])
    return bool(_VIP_RE.search(blob))


def _parse_loyalty(bonus: Any) -> Optional[float]:
    if bonus is None or bonus == "":
        return None
    if isinstance(bonus, (int, float)):
        return float(bonus)
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(bonus))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except ValueError:
        return None


def find_row_in_catalog(catalog: dict[str, Any], client_id: str) -> Optional[dict[str, Any]]:
    cid = (client_id or "").strip()
    if not cid:
        return None
    for row in catalog.get("rows") or []:
        if str(row.get("_moysklad_id") or "").strip() == cid:
            return row
    return None


def build_client_detail(row: dict[str, Any]) -> dict[str, Any]:
    """Map an enriched catalog row into the client-card API payload (no LLM)."""
    public = _public_client(row)
    orders_raw = list(row.get("_orders_context") or [])
    orders = [_order_public(o) for o in orders_raw if isinstance(o, dict)]
    orders.sort(key=lambda o: o.get("date") or "", reverse=True)

    tags = list(public.get("tags") or [])
    tag_buckets = _split_tags(tags)
    vip = _is_vip(tags, str(public.get("state") or ""))
    loyalty = _parse_loyalty(public.get("bonus_points"))
    msg = messaging_links(
        phone=str(public.get("phone") or ""),
        tg_nick=str(public.get("tg_nick") or ""),
        tg_conversation=str(public.get("tg_conversation") or ""),
    )

    last = orders[0] if orders else None
    data_thin = len(orders) < 2 and not (public.get("phone") or public.get("tg_nick"))

    return {
        "ok": True,
        "client": {
            **public,
            "vip": vip,
            "loyalty_points": loyalty,
            "primary_channel": msg.get("primary_channel") or "",
            "tag_buckets": tag_buckets,
            "description": str(row.get("description") or ""),
        },
        "orders": orders,
        "stats": {
            "avg_check": float(public.get("avg_check") or 0),
            "order_count": int(public.get("order_count") or len(orders)),
            "vip": vip,
            "loyalty_points": loyalty,
            "last_order": last,
        },
        "messaging": msg,
        "data_thin": data_thin,
        "ai": heuristic_ai(public, orders, vip=vip, loyalty=loyalty, data_thin=data_thin),
    }


def heuristic_ai(
    client: dict[str, Any],
    orders: list[dict[str, Any]],
    *,
    vip: bool,
    loyalty: Optional[float],
    data_thin: bool,
) -> dict[str, Any]:
    """Deterministic summary/recommendation from facts only (no LLM)."""
    name = client.get("name") or "Клиент"
    n = len(orders)
    avg = float(client.get("avg_check") or 0)
    channels = sorted({str(o.get("channel") or "") for o in orders if o.get("channel")})
    months = []
    for o in orders:
        d = str(o.get("date") or "")
        if len(d) >= 7:
            months.append(d[5:7])

    facts = []
    if n:
        facts.append(f"в кэше {n} заказ(ов)")
        if avg:
            facts.append(f"средний чек ≈ {avg:.0f} ₽")
        if channels:
            facts.append("каналы: " + ", ".join(channels))
        sample = orders[:3]
        cites = []
        for o in sample:
            bit = (o.get("date") or "?")[:10]
            if o.get("sum"):
                bit += f" / {o['sum']:.0f} ₽"
            if o.get("channel"):
                bit += f" ({o['channel']})"
            cites.append(bit)
        if cites:
            facts.append("примеры: " + "; ".join(cites))
    else:
        facts.append("заказов в выгрузке нет")

    contact_bits = []
    if client.get("phone"):
        contact_bits.append("телефон есть")
    if client.get("tg_nick") or client.get("tg_conversation"):
        contact_bits.append("Telegram есть")
    if not contact_bits:
        contact_bits.append("контактов для мессенджеров нет")

    tags = client.get("tags") or []
    event_tags = [t for t in tags if _EVENT_TAG_RE.search(str(t))]
    occasion_parts = []
    if event_tags:
        occasion_parts.append("теги событий: " + ", ".join(str(t) for t in event_tags))
    if "03" in months:
        occasion_parts.append("есть заказы в марте — возможен повод 8 Марта (по факту дат)")
    if "02" in months:
        occasion_parts.append("есть заказы в феврале — возможен День святого Валентина")
    if "09" in months:
        occasion_parts.append("есть заказы в сентябре — возможен 1 сентября / День знаний")
    if "12" in months or "01" in months:
        occasion_parts.append("есть заказы около НГ — возможен новогодний спрос")
    if not occasion_parts:
        occasion_parts.append(
            "явного повода в тегах/месяцах не видно — не приписываем праздник"
        )

    history = (
        f"{name}: " + "; ".join(facts) + ". "
        + ("VIP по тегам/статусу. " if vip else "VIP в данных не отмечен. ")
        + (
            f"Лояльность (баллы): {loyalty:.0f}. "
            if loyalty is not None
            else "баллы лояльности в данных не указаны. "
        )
        + "Контакты: " + ", ".join(contact_bits) + "."
    )
    if data_thin:
        history += " Данных мало — выводы осторожные."

    occasion = " ".join(occasion_parts) + "."

    if n and avg:
        rec = (
            f"Связаться за ~5 дней до ожидаемого повода/доставки "
            f"(опираясь на даты последних заказов). "
            f"Ориентир чека из истории ≈ {avg:.0f} ₽. "
            f"Предложить букет/композицию в духе прошлых позиций "
            f"(см. сниппеты заказов), без выдуманных SKU. "
        )
    elif n:
        rec = (
            "Есть заказы, но средний чек не посчитан — уточнить бюджет у клиента, "
            "не называть сумму наугад. Связаться в окно ~5 дней до повода, "
            "если повод подтверждён фактами."
        )
    else:
        rec = (
            "Заказов нет — сначала мягкий контакт (если есть телефон/Telegram), "
            "не обещать историю покупок и не назначать VIP."
        )
    if not (client.get("phone") or client.get("tg_nick") or client.get("tg_conversation")):
        rec += " Канала для WhatsApp/Telegram в карточке нет — не предлагать отправку туда."

    return {
        "history_profile": history,
        "occasion_intent": occasion,
        "recommendation": rec,
        "source": "heuristic",
        "data_thin": data_thin,
    }


def _facts_payload(detail: dict[str, Any]) -> dict[str, Any]:
    client = detail.get("client") or {}
    return {
        "client": {
            "id": client.get("id"),
            "name": client.get("name"),
            "phone": client.get("phone") or None,
            "email": client.get("email") or None,
            "tg_nick": client.get("tg_nick") or None,
            "tg_conversation": client.get("tg_conversation") or None,
            "state": client.get("state") or None,
            "company_type": client.get("company_type") or None,
            "sex": client.get("sex") or None,
            "role": client.get("role") or None,
            "tags": client.get("tags") or [],
            "channels": client.get("channels") or [],
            "sales_type": client.get("sales_type") or None,
            "vip": bool(client.get("vip")),
            "loyalty_points": client.get("loyalty_points"),
            "avg_check": client.get("avg_check"),
            "order_count": client.get("order_count"),
            "last_order_at": client.get("last_order_at") or None,
            "primary_channel": client.get("primary_channel") or None,
        },
        "orders": [
            {
                "id": o.get("id"),
                "date": o.get("date"),
                "sum": o.get("sum"),
                "channel": o.get("channel") or None,
                "product_snippet": o.get("product_snippet") or None,
            }
            for o in (detail.get("orders") or [])[:40]
        ],
        "data_thin": bool(detail.get("data_thin")),
    }


def _parse_ai_json(text: str) -> Optional[dict[str, Any]]:
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
    out = {
        "history_profile": str(data.get("history_profile") or "").strip(),
        "occasion_intent": str(data.get("occasion_intent") or "").strip(),
        "recommendation": str(data.get("recommendation") or "").strip(),
    }
    if not any(out.values()):
        return None
    return out


def generate_ai_for_detail(detail: dict[str, Any]) -> dict[str, Any]:
    """Call auxiliary LLM; fall back to heuristic on any failure."""
    client = detail.get("client") or {}
    orders = list(detail.get("orders") or [])
    vip = bool(client.get("vip"))
    loyalty = client.get("loyalty_points")
    data_thin = bool(detail.get("data_thin"))
    fallback = heuristic_ai(
        client, orders, vip=vip, loyalty=loyalty, data_thin=data_thin
    )

    facts = _facts_payload(detail)
    user = (
        "JSON фактов клиента и заказов (единственный источник истины):\n"
        + json.dumps(facts, ensure_ascii=False, indent=2)
    )
    try:
        from agent.auxiliary_client import call_llm, extract_content_or_reasoning

        response = call_llm(
            task="compression",
            messages=[
                {"role": "system", "content": _AI_SYSTEM},
                {"role": "user", "content": user},
            ],
            max_tokens=900,
            temperature=0.2,
            timeout=45.0,
        )
        text = (extract_content_or_reasoning(response) or "").strip()
        parsed = _parse_ai_json(text)
        if not parsed:
            log.warning("moysklad client AI: empty/unparsed response, using heuristic")
            return fallback
        return {
            **parsed,
            "source": "llm",
            "data_thin": data_thin,
        }
    except Exception as exc:
        log.warning("moysklad client AI unavailable: %s", exc)
        return {**fallback, "source": "heuristic", "error": str(exc)}
