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
from plugins.moysklad.sales_channels import display_channel_label

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

_MONTH_LABELS_RU = {
    "01": "январь",
    "02": "февраль",
    "03": "март",
    "04": "апрель",
    "05": "май",
    "06": "июнь",
    "07": "июль",
    "08": "август",
    "09": "сентябрь",
    "10": "октябрь",
    "11": "ноябрь",
    "12": "декабрь",
}

_DEBT_TAG_RE = re.compile(
    r"(долг|задолжен|неопл|просроч|коллекц|дебитор|к\s*оплате|не\s*плат)",
    re.IGNORECASE,
)

_AI_SYSTEM = """Ты — помощник продавца цветочного магазина (квіти/цветы), B2B и розница.
Пиши рекомендации СВОБОДНО и полезно — как умный коллега в чате: конкретика,
вкус, следующий шаг. Без канцелярита и без «робота CRM». Отвечай на русском.

Опора на данные (якорь, не смирительная рубашка):
1. Имя, заказы, даты, суммы, каналы, статусы заказов (доставлен/отменён/оплачен),
   теги, VIP, долг — только из JSON. Не выдумывай телефон, email, Telegram,
   скидки, акции, адреса.
2. Цитируй реальные даты/суммы/букеты из истории, когда они есть.
   Состав заказа — поля orders[].composition / orders[].line_items
   (позиции номенклатуры МойСклад). Если состав есть — ОБЯЗАТЕЛЬНО опирайся
   на него в recommendation (назови цветы/букет словами из состава).
   Не подменяй состав кодом заказа вроде «1605-02».
3. Если в JSON есть conversation / conversation_preview / tg_conversation —
   ОБЯЗАТЕЛЬНО учти переписку Telegram: тон, просьбы, жалобы, договорённости.
   Не пиши «переписки нет», если message_count > 0 или preview непустой.
4. Если данных мало — скажи прямо «данных мало», всё равно дай аккуратную гипотезу.
5. Рекомендации: когда связаться, что предложить, ориентир чека — из среднего чека
   и истории; можно креативно упаковать, но без фейковых промо.
6. Праздники/поводы — если месяцы заказов, теги, описание или переписка это поддерживают.
   Известные поводы: """ + ", ".join(_RU_OCCASIONS) + """.
7. Не предлагай каналы связи, которых нет в JSON.
7а. БАЛЛЫ ЛОЯЛЬНОСТИ (client.loyalty_points): если баллы есть и > 0 —
   ОБЯЗАТЕЛЬНО учти их в recommendation: напомни, что накоплено N баллов и их
   можно потратить при следующем заказе. Курс баллов не выдумывай; если
   баллов нет или 0 — не упоминай их вовсе.
8. РИСКИ / ДОЛГ (risks): если has_debt / unpaid_order_count / do_not_upsell —
   в recommendation сначала сверка оплаты / задолженность, не дорогой upsell.
   Долг не выдумывай. Отменённые заказы (payment_status=cancelled) — не считай покупкой.
9. Ответ — строго JSON без markdown:
{
  "history_profile": "2-6 предложений: история и профиль",
  "occasion_intent": "2-6 предложений: повод/intent, сезонность, окна касания",
  "recommendation": "2-8 предложений: что и когда предложить продавцу — живо и по делу"
}
"""


def _parse_balance_rub(raw: Any) -> Optional[float]:
    """MoySklad balance may already be rub (catalog) or kopecks (raw API)."""
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def compute_risks(
    client: dict[str, Any],
    orders: list[dict[str, Any]],
    *,
    data_thin: bool = False,
) -> dict[str, Any]:
    """Grounded debt / unpaid / upsell-block flags. Never invents debt.

    Stale unpaid / cancelled-only history → ``failed_customer`` (not payment chase).
    Recent unpaid or balance debt → ``do_not_upsell`` + payment nudge.
    """
    from plugins.moysklad.order_status import (
        classify_order_payment,
        order_is_recent,
        summarize_order_context,
    )

    balance = _parse_balance_rub(client.get("balance"))
    # Negative balance ⇒ client owes the company (MoySklad Remap semantics).
    has_debt = balance is not None and balance < -0.009
    debt_amount = round(abs(balance), 2) if has_debt else 0.0

    payment = summarize_order_context(orders)
    unpaid_orders: list[dict[str, Any]] = []
    recent_unpaid_orders: list[dict[str, Any]] = []
    unpaid_total = 0.0
    recent_unpaid_total = 0.0
    for o in orders or []:
        status = classify_order_payment(o)
        if status == "cancelled":
            continue
        unpaid = o.get("unpaid")
        if unpaid is None and status != "unpaid":
            continue
        try:
            unpaid_f = float(unpaid) if unpaid is not None else (
                float(o.get("sum") or 0) if status == "unpaid" else 0.0
            )
        except (TypeError, ValueError):
            continue
        if unpaid_f <= 0.009 and status != "unpaid":
            continue
        if unpaid_f <= 0.009:
            continue
        entry = {
            "id": o.get("id"),
            "date": (o.get("date") or o.get("moment") or "")[:16],
            "sum": o.get("sum"),
            "unpaid": round(unpaid_f, 2),
            "payment_status": status,
            "product_snippet": o.get("product_snippet") or None,
            "recent": order_is_recent(o),
        }
        unpaid_orders.append(entry)
        unpaid_total += unpaid_f
        if entry["recent"]:
            recent_unpaid_orders.append(entry)
            recent_unpaid_total += unpaid_f

    tags_blob = " ".join(str(t) for t in (client.get("tags") or []))
    state_blob = str(client.get("state") or "")
    debt_tag = bool(_DEBT_TAG_RE.search(tags_blob + " " + state_blob))

    failed_customer = bool(
        payment.get("failed_only")
        or (
            unpaid_orders
            and not recent_unpaid_orders
            and not has_debt
            and int(payment.get("paid_order_count") or 0) <= 0
        )
        or "несостояв" in state_blob.lower().replace("ё", "е")
    )
    # Recent unpaid with zero paid → still chase payment, not «несостоявшийся».
    if recent_unpaid_orders and int(payment.get("paid_order_count") or 0) <= 0:
        failed_customer = False
    # Payment chase only for live debt / recent unpaid — not abandoned 2025 carts.
    do_not_upsell = bool(has_debt or recent_unpaid_orders or (debt_tag and not failed_customer))
    flags: list[str] = []
    if has_debt:
        flags.append(f"долг по балансу ≈ {debt_amount:.0f} ₽")
    if recent_unpaid_orders:
        flags.append(
            f"свежих неоплаченных заказов: {len(recent_unpaid_orders)} "
            f"(≈ {recent_unpaid_total:.0f} ₽)"
        )
    elif unpaid_orders:
        flags.append(
            f"старые неоплаченные/сорвавшиеся заказы: {len(unpaid_orders)} "
            f"(≈ {unpaid_total:.0f} ₽) — не гонять «где оплата?»"
        )
    if failed_customer:
        flags.append("несостоявшийся клиент (оплаченных заказов нет)")
    if debt_tag and not failed_customer:
        flags.append("в тегах/статусе есть признак долга/неоплаты")
    if data_thin:
        flags.append("тонкая история — осторожные выводы")

    return {
        "balance": balance,
        "has_debt": has_debt,
        "debt_amount": debt_amount if has_debt else None,
        "unpaid_order_count": len(unpaid_orders),
        "unpaid_total": round(unpaid_total, 2) if unpaid_orders else 0.0,
        "recent_unpaid_count": len(recent_unpaid_orders),
        "recent_unpaid_total": round(recent_unpaid_total, 2) if recent_unpaid_orders else 0.0,
        "unpaid_orders_preview": (recent_unpaid_orders or unpaid_orders)[:5],
        "paid_order_count": int(payment.get("paid_order_count") or 0),
        "cancelled_order_count": int(payment.get("cancelled_order_count") or 0),
        "failed_customer": failed_customer,
        "customer_outcome": payment.get("customer_outcome") or "none",
        "debt_tag": debt_tag,
        "do_not_upsell": do_not_upsell,
        "data_thin_warning": bool(data_thin),
        "flags": flags,
    }


def _seasonality_months(orders: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for o in orders or []:
        d = str(o.get("date") or "")
        if len(d) >= 7:
            mm = d[5:7]
            label = _MONTH_LABELS_RU.get(mm)
            if label and label not in seen:
                seen.append(label)
    return seen


def _touch_windows(months: list[str], event_tags: list[str]) -> list[str]:
    """Human touch-window hints grounded only on months/tags present."""
    windows: list[str] = []
    joined = " ".join(months).lower()
    tags_l = " ".join(event_tags).lower()
    if "март" in joined or "март" in tags_l or "8" in tags_l:
        windows.append("~5 дней до 8 Марта")
    if "феврал" in joined or "валентин" in tags_l:
        windows.append("~5 дней до 14 февраля")
    if "сентябр" in joined or "сентябр" in tags_l or "знан" in tags_l:
        windows.append("~5 дней до 1 сентября")
    if "декабр" in joined or "январ" in joined or "новый" in tags_l:
        windows.append("окно перед НГ (декабрь)")
    return windows


def build_fact_blocks(detail: dict[str, Any]) -> dict[str, Any]:
    """Three structured audit blocks for «Факты клиента» (not AI prose)."""
    client = detail.get("client") or {}
    stats = detail.get("stats") or {}
    orders = list(detail.get("orders") or [])
    risks = detail.get("risks") or compute_risks(
        client, orders, data_thin=bool(detail.get("data_thin"))
    )
    buckets = client.get("tag_buckets") or {}
    event_tags = list(buckets.get("events") or [])
    if not event_tags:
        event_tags = [
            t
            for t in (client.get("tags") or [])
            if _EVENT_TAG_RE.search(str(t))
        ]
    channels = list(client.get("channels") or [])
    seasonality = _seasonality_months(orders)
    role = str(client.get("role") or "").strip()
    order_count = int(stats.get("order_count") or client.get("order_count") or 0)
    avg = float(stats.get("avg_check") or client.get("avg_check") or 0)
    vip = bool(stats.get("vip") or client.get("vip"))

    history_lines: list[dict[str, str]] = []
    if order_count:
        history_lines.append({"label": "Заказов", "value": str(order_count)})
    if channels:
        history_lines.append({"label": "Каналы", "value": ", ".join(channels)})
    compositions = []
    for o in orders[:5]:
        comp = str(o.get("composition") or "").strip()
        if not comp:
            items = o.get("line_items") or []
            if isinstance(items, list) and items:
                comp = "; ".join(str(x) for x in items if str(x).strip())
        if not comp:
            continue
        bit = (str(o.get("date") or "")[:10] + " — " if o.get("date") else "") + comp
        compositions.append(bit)
    if compositions:
        history_lines.append({"label": "Состав заказов", "value": " · ".join(compositions)})
    if avg > 0:
        history_lines.append({"label": "Средний чек", "value": f"{avg:.0f} ₽"})
    history_lines.append({"label": "VIP / статус", "value": "VIP" if vip else (str(client.get("state") or "").strip() or "не отмечен")})
    if seasonality:
        history_lines.append({"label": "Сезонность", "value": ", ".join(seasonality)})

    occasion_lines: list[dict[str, str]] = []
    if event_tags:
        occasion_lines.append({"label": "Теги повода", "value": ", ".join(event_tags)})
    windows = _touch_windows(seasonality, event_tags)
    if windows:
        occasion_lines.append({"label": "Окна касания", "value": "; ".join(windows)})
    if role:
        occasion_lines.append({"label": "Роль", "value": role})

    risk_lines: list[dict[str, str]] = []
    if risks.get("has_debt") and risks.get("debt_amount") is not None:
        risk_lines.append(
            {"label": "Долг (баланс)", "value": f"{float(risks['debt_amount']):.0f} ₽"}
        )
    elif risks.get("balance") is not None:
        bal = float(risks["balance"])
        risk_lines.append(
            {
                "label": "Баланс",
                "value": f"{bal:.0f} ₽" + (" (долга нет)" if bal >= 0 else ""),
            }
        )
    if risks.get("unpaid_order_count"):
        risk_lines.append(
            {
                "label": "Неоплаченные заказы",
                "value": (
                    f"{risks['unpaid_order_count']} "
                    f"(≈ {float(risks.get('unpaid_total') or 0):.0f} ₽)"
                ),
            }
        )
    if risks.get("do_not_upsell"):
        risk_lines.append(
            {"label": "Upsell", "value": "не предлагать дорогие букеты"}
        )
    if risks.get("data_thin_warning"):
        risk_lines.append({"label": "История", "value": "данных мало"})

    return {
        "history_profile": {
            "title": "История и профиль",
            "empty": not history_lines,
            "lines": history_lines,
            "note": None if history_lines else "Нет данных по заказам/профилю",
        },
        "occasion_intent": {
            "title": "Повод и intent",
            "empty": not occasion_lines,
            "lines": occasion_lines,
            "note": None
            if occasion_lines
            else "Повода/роли в тегах и месяцах не видно",
        },
        "risks": {
            "title": "Риски / ограничения",
            "empty": not risk_lines,
            "lines": risk_lines,
            "note": None
            if risk_lines
            else "Долг и неоплаченные заказы в данных не зафиксированы",
            "do_not_upsell": bool(risks.get("do_not_upsell")),
        },
    }


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
            "Чат и отправка — через Telegram Business bot "
            "(MOYSKLAD_TELEGRAM_BOT_TOKEN) или WhatsApp deep-link. "
            "Рассылки: кнопка «Отправить в Telegram»."
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
    channel_label = display_channel_label(channel)
    name = str(item.get("name") or "").strip()
    desc = str(item.get("description") or "").strip()
    snippet = str(item.get("product_snippet") or "").strip()
    composition = str(item.get("composition") or "").strip()
    line_items_raw = item.get("line_items")
    line_items: list[str] = []
    if isinstance(line_items_raw, list):
        line_items = [str(x).strip() for x in line_items_raw if str(x or "").strip()]
    if not composition and line_items:
        composition = "; ".join(line_items)
    if not snippet:
        snippet = composition or (desc or name)[:120]
    payed_raw = item.get("payed_sum")
    unpaid_raw = item.get("unpaid")
    payed_f: Optional[float] = None
    unpaid_f: Optional[float] = None
    if payed_raw is not None and payed_raw != "":
        try:
            payed_f = round(float(payed_raw), 2)
        except (TypeError, ValueError):
            payed_f = None
    if unpaid_raw is not None and unpaid_raw != "":
        try:
            unpaid_f = round(float(unpaid_raw), 2)
        except (TypeError, ValueError):
            unpaid_f = None
    return {
        "id": str(item.get("id") or "").strip(),
        "name": name,
        "date": moment,
        "sum": round(amount_f, 2),
        "payed_sum": payed_f,
        "unpaid": unpaid_f,
        "state": str(item.get("state") or "").strip() or None,
        "applicable": item.get("applicable") if isinstance(item.get("applicable"), bool) else None,
        "payment_status": str(
            item.get("payment_status")
            or classify_order_payment_safe(item, payed_f, unpaid_f)
        ),
        "channel": channel_label,
        "product_snippet": snippet,
        "composition": composition or None,
        "line_items": line_items,
        "description": desc,
    }


def classify_order_payment_safe(
    item: dict[str, Any],
    payed_f: Optional[float],
    unpaid_f: Optional[float],
) -> str:
    from plugins.moysklad.order_status import classify_order_payment

    stamped = dict(item)
    if payed_f is not None:
        stamped["payed_sum"] = payed_f
    if unpaid_f is not None:
        stamped["unpaid"] = unpaid_f
    return classify_order_payment(stamped)


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


def build_client_detail(
    row: dict[str, Any],
    *,
    ms_client: Any = None,
    fetch_positions: Any = None,
    enrich_compositions: bool = True,
    max_composition_orders: int = 8,
) -> dict[str, Any]:
    """Map an enriched catalog row into the client-card API payload (no LLM).

    Recent orders are enriched with MoySklad positions → ``composition`` /
    ``line_items`` so the UI and AI recommendations can name real bouquet
    составы. Pass ``ms_client`` / ``fetch_positions`` explicitly, or leave
    both unset to auto-use ``MoySkladClient`` when a token is configured.
    """
    if enrich_compositions and ms_client is None and fetch_positions is None:
        try:
            from plugins.moysklad.client import MoySkladClient, token_configured

            if token_configured():
                ms_client = MoySkladClient()
        except Exception:
            log.debug("moysklad client for composition enrich unavailable", exc_info=True)

    if enrich_compositions and (ms_client is not None or fetch_positions is not None):
        try:
            from plugins.moysklad.order_compositions import enrich_row_order_compositions

            enrich_row_order_compositions(
                row,
                ms_client=ms_client,
                fetch_positions=fetch_positions,
                max_orders=max_composition_orders,
            )
        except Exception:
            log.debug("order composition enrich failed", exc_info=True)

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
    client_body = {
        **public,
        "vip": vip,
        "loyalty_points": loyalty,
        "primary_channel": msg.get("primary_channel") or "",
        "tag_buckets": tag_buckets,
        "description": str(row.get("description") or ""),
    }
    risks = compute_risks(client_body, orders, data_thin=data_thin)
    detail = {
        "ok": True,
        "client": client_body,
        "orders": orders,
        "stats": {
            "avg_check": float(public.get("avg_check") or 0),
            "order_count": int(public.get("order_count") or len(orders)),
            "paid_order_count": int(public.get("paid_order_count") or 0),
            "cancelled_order_count": int(public.get("cancelled_order_count") or 0),
            "unpaid_order_count": int(public.get("unpaid_order_count") or 0),
            "fulfilled_order_count": int(public.get("fulfilled_order_count") or 0),
            "vip": vip,
            "loyalty_points": loyalty,
            "last_order": last,
            "balance": client_body.get("balance"),
            "has_debt": bool(risks.get("has_debt")),
            "do_not_upsell": bool(risks.get("do_not_upsell")),
        },
        "messaging": msg,
        "data_thin": data_thin,
        "risks": risks,
        "ai": heuristic_ai(
            client_body,
            orders,
            vip=vip,
            loyalty=loyalty,
            data_thin=data_thin,
            risks=risks,
        ),
    }
    detail["fact_blocks"] = build_fact_blocks(detail)
    try:
        from plugins.moysklad.conversations import conversation_for_detail

        detail["conversation"] = conversation_for_detail(detail)
        # Параметр «последний контакт через ТГ» — на карточке клиента.
        lc = str(
            (detail.get("conversation") or {}).get("last_contact_at") or ""
        ).strip()
        if not lc:
            try:
                from plugins.moysklad.conversations import enrich_client_row

                stamped = enrich_client_row(dict(detail.get("client") or {}))
                lc = str(stamped.get("tg_last_contact_at") or "").strip()
            except Exception:
                lc = ""
        detail["client"]["tg_last_contact_at"] = lc
        sync_meta = (detail.get("conversation") or {}).get("sync") or {}
        # New inbound replies after a mass send → refresh recommendation so
        # Facts / card don't keep the pre-reply tip.
        if int(sync_meta.get("inbound_imported") or 0) > 0:
            try:
                detail["ai"] = generate_ai_for_detail(detail)
            except Exception:
                log.debug("AI refresh after inbound sync failed", exc_info=True)
    except Exception:  # pragma: no cover — store must not break card
        detail["conversation"] = {
            "messages": [],
            "message_count": 0,
            "preview": "",
            "empty": True,
        }
    return detail


def heuristic_ai(
    client: dict[str, Any],
    orders: list[dict[str, Any]],
    *,
    vip: bool,
    loyalty: Optional[float],
    data_thin: bool,
    risks: Optional[dict[str, Any]] = None,
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
    risks = risks or compute_risks(client, orders, data_thin=data_thin)

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
            comp = str(o.get("composition") or "").strip()
            if not comp and isinstance(o.get("line_items"), list):
                comp = "; ".join(str(x) for x in o["line_items"] if str(x).strip())
            if comp:
                bit += f" · состав: {comp[:80]}"
            elif o.get("product_snippet"):
                bit += f" · {str(o['product_snippet'])[:60]}"
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
    if risks.get("has_debt") and risks.get("debt_amount") is not None:
        history += f" В данных долг ≈ {float(risks['debt_amount']):.0f} ₽."

    occasion = " ".join(occasion_parts) + "."

    if risks.get("failed_customer"):
        rec = (
            "Несостоявшийся клиент: оплаченных заказов нет "
            "(неоплата / отмена / сорвавшийся чекаут). "
            "НЕ спрашивать «где оплата?» и не ссылаться на старый заказ как на покупку. "
            "Мягкий re-contact без истории оплат, либо пропустить в рассылке «оплата»."
        )
    elif risks.get("do_not_upsell"):
        bits = []
        if risks.get("has_debt") and risks.get("debt_amount") is not None:
            bits.append(f"долг ≈ {float(risks['debt_amount']):.0f} ₽")
        if risks.get("recent_unpaid_count"):
            bits.append(
                f"свежих неоплаченных: {risks['recent_unpaid_count']} "
                f"(≈ {float(risks.get('recent_unpaid_total') or 0):.0f} ₽)"
            )
        rec = (
            "НЕ предлагать дорогие букеты / upsell. "
            "Сначала мягко напомнить о сверке оплаты / закрытии задолженности"
            + (f" ({'; '.join(bits)})" if bits else "")
            + ". Тон спокойный, без давления; скидки и суммы долга не выдумывать."
        )
    elif n and avg:
        comps = [
            str(o.get("composition") or o.get("product_snippet") or "").strip()
            for o in orders[:3]
        ]
        comps = [c for c in comps if c]
        bouquet_hint = (
            f"Опираться на прошлый состав: {comps[0][:100]}. "
            if comps
            else "Предложить букет/композицию в духе прошлых позиций (см. состав заказов), без выдуманных SKU. "
        )
        rec = (
            f"Связаться за ~5 дней до ожидаемого повода/доставки "
            f"(опираясь на даты последних заказов). "
            f"Ориентир чека из истории ≈ {avg:.0f} ₽. "
            f"{bouquet_hint}"
        )
    elif n:
        comps = [
            str(o.get("composition") or o.get("product_snippet") or "").strip()
            for o in orders[:3]
        ]
        comps = [c for c in comps if c]
        bouquet_bit = (
            f" Опираться на прошлый состав: {comps[0][:100]}."
            if comps
            else ""
        )
        rec = (
            "Есть заказы, но средний чек не посчитан — уточнить бюджет у клиента, "
            "не называть сумму наугад. Связаться в окно ~5 дней до повода, "
            f"если повод подтверждён фактами.{bouquet_bit}"
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
        "do_not_upsell": bool(risks.get("do_not_upsell")),
        "failed_customer": bool(risks.get("failed_customer")),
    }


def _facts_payload(detail: dict[str, Any]) -> dict[str, Any]:
    client = detail.get("client") or {}
    orders = list(detail.get("orders") or [])
    risks = detail.get("risks") or compute_risks(
        client, orders, data_thin=bool(detail.get("data_thin"))
    )
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
            "balance": client.get("balance"),
        },
        "orders": [
            {
                "id": o.get("id"),
                "date": o.get("date"),
                "sum": o.get("sum"),
                "payed_sum": o.get("payed_sum"),
                "unpaid": o.get("unpaid"),
                "state": o.get("state") or None,
                "payment_status": o.get("payment_status") or None,
                "channel": o.get("channel") or None,
                "product_snippet": o.get("product_snippet") or None,
                "composition": o.get("composition") or None,
                "line_items": list(o.get("line_items") or [])[:12],
            }
            for o in orders[:40]
        ],
        "risks": {
            "has_debt": bool(risks.get("has_debt")),
            "debt_amount": risks.get("debt_amount"),
            "balance": risks.get("balance"),
            "unpaid_order_count": int(risks.get("unpaid_order_count") or 0),
            "unpaid_total": risks.get("unpaid_total"),
            "do_not_upsell": bool(risks.get("do_not_upsell")),
            "flags": list(risks.get("flags") or []),
        },
        "conversation": {
            "message_count": int(
                (detail.get("conversation") or {}).get("message_count") or 0
            ),
            "preview": (detail.get("conversation") or {}).get("preview") or "",
            "messages": list(
                (detail.get("conversation") or {}).get("messages") or []
            )[-20:],
            "tg_conversation_attr": client.get("tg_conversation") or "",
            "tg_nick": client.get("tg_nick") or "",
        },
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


def generate_ai_for_detail(
    detail: dict[str, Any],
    *,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Call auxiliary LLM; fall back to heuristic on any failure.

    Default model is DeepSeek via OpenRouter (UI summary is DeepSeek-only).
    Optional ``provider`` / ``model`` still allow overrides for tests.
    """
    provider = (provider or "").strip() or "openrouter"
    model = (model or "").strip() or "deepseek/deepseek-chat"
    client = detail.get("client") or {}
    orders = list(detail.get("orders") or [])
    vip = bool(client.get("vip"))
    loyalty = client.get("loyalty_points")
    data_thin = bool(detail.get("data_thin"))
    risks = detail.get("risks") or compute_risks(client, orders, data_thin=data_thin)
    fallback = heuristic_ai(
        client,
        orders,
        vip=vip,
        loyalty=loyalty,
        data_thin=data_thin,
        risks=risks,
    )

    facts = _facts_payload(detail)
    # Include conversation preview so summary can use chat history.
    conversation = detail.get("conversation") or {}
    messages = list(conversation.get("messages") or [])[-40:]
    preview_msgs = [
        {
            "direction": m.get("direction"),
            "text": str(m.get("text") or "")[:400],
            "ts": m.get("ts"),
        }
        for m in messages
        if str(m.get("text") or "").strip()
    ]
    if preview_msgs:
        facts["conversation_preview"] = preview_msgs[-24:]
    # Even without structured messages, surface TG link / attr so the model
    # does not claim «переписки нет» when the CRM column shows a chat.
    tg_attr = str(client.get("tg_conversation") or "").strip()
    tg_nick = str(client.get("tg_nick") or "").strip()
    if tg_attr or tg_nick or preview_msgs:
        facts["telegram"] = {
            "tg_nick": tg_nick or None,
            "tg_conversation": tg_attr or None,
            "message_count": int(conversation.get("message_count") or len(preview_msgs)),
            "has_thread": bool(preview_msgs) or int(conversation.get("message_count") or 0) > 0,
        }
    user = (
        "JSON фактов клиента и заказов (единственный источник истины):\n"
        + json.dumps(facts, ensure_ascii=False, indent=2)
    )
    try:
        from agent.auxiliary_client import call_llm, extract_content_or_reasoning

        kwargs: dict[str, Any] = {
            "task": "compression",
            "messages": [
                {"role": "system", "content": _AI_SYSTEM},
                {"role": "user", "content": user},
            ],
            "max_tokens": 900,
            "temperature": 0.2,
            "timeout": 45.0,
            "provider": provider,
            "model": model,
        }
        response = call_llm(**kwargs)
        text = (extract_content_or_reasoning(response) or "").strip()
        parsed = _parse_ai_json(text)
        if not parsed:
            log.warning("moysklad client AI: empty/unparsed response, using heuristic")
            return {**fallback, "provider": provider, "model": model}
        return {
            **parsed,
            "source": "llm",
            "data_thin": data_thin,
            "provider": provider,
            "model": model,
        }
    except Exception as exc:
        log.warning("moysklad client AI unavailable: %s", exc)
        return {
            **fallback,
            "source": "heuristic",
            "error": str(exc),
            "provider": provider,
            "model": model,
        }
