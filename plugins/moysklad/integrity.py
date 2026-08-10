"""Проверка таблицы Клиенты — what is broken, wrong, or unreachable.

``classify.catalog_integrity`` only explains tab arithmetic. This module walks
the deduped catalog once and reports concrete, actionable defects: duplicate
contacts that survived dedupe, clients nobody can be reached at, order money
that contradicts the order count, dates that will never fire a reminder.

Every issue carries a ``sample`` of real rows so the operator can open the card
and fix it — nothing here writes to MoySklad.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Callable, Iterable

from plugins.moysklad.audience import row_client_stage, row_has_phone, row_has_telegram
from plugins.moysklad.dedupe import normalize_email, normalize_phone, normalize_telegram
from plugins.moysklad.sales_channels import unique_sales_channels

#: Rows listed per issue — enough to act on, small enough to render.
SAMPLE_SIZE = 12

SEVERITY_ERROR = "error"
SEVERITY_WARN = "warn"
SEVERITY_INFO = "info"

_DIGITS = re.compile(r"\D+")
# RU mobile/landline after normalization is 10 digits; anything else is suspect.
_PLAUSIBLE_PHONE_LEN = 10


def _row_id(row: dict[str, Any]) -> str:
    return str(row.get("_moysklad_id") or row.get("id") or "").strip()


def _row_name(row: dict[str, Any]) -> str:
    return str(row.get("Наименование") or row.get("name") or "").strip()


def _sample(rows: Iterable[dict[str, Any]], detail: Callable[[dict[str, Any]], str]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in rows:
        out.append({
            "id": _row_id(row),
            "name": _row_name(row) or "(без имени)",
            "detail": detail(row),
        })
        if len(out) >= SAMPLE_SIZE:
            break
    return out


def _parse_date(raw: Any) -> date | None:
    text = str(raw or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[: len(fmt) + 2].strip(), fmt).date()
        except ValueError:
            continue
    return None


def _issue(
    code: str,
    label: str,
    severity: str,
    rows: list[dict[str, Any]],
    detail: Callable[[dict[str, Any]], str],
    *,
    hint: str = "",
) -> dict[str, Any]:
    return {
        "code": code,
        "label": label,
        "severity": severity,
        "count": len(rows),
        "hint": hint,
        "sample": _sample(rows, detail),
    }


def _duplicate_issues(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Contacts shared by more than one card — dedupe should have merged them."""
    by_phone: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_email: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_tg: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        phone = normalize_phone(row.get("Телефон") or row.get("phone"))
        if phone:
            by_phone[phone].append(row)
        email = normalize_email(row.get("email") or row.get("E-mail"))
        if email:
            by_email[email].append(row)
        nick = normalize_telegram(row.get("ТГ ник") or row.get("tg_nick"))
        if nick:
            by_tg[nick].append(row)

    issues: list[dict[str, Any]] = []
    for code, label, index, key_name in (
        ("dup_phone", "Один телефон у нескольких карточек", by_phone, "телефон"),
        ("dup_email", "Один e-mail у нескольких карточек", by_email, "e-mail"),
        ("dup_telegram", "Один ТГ ник у нескольких карточек", by_tg, "ник"),
    ):
        clashes = {k: v for k, v in index.items() if len(v) > 1}
        if not clashes:
            continue
        affected = [row for group in clashes.values() for row in group]
        key_by_id = {
            _row_id(row): k for k, group in clashes.items() for row in group
        }
        issues.append(
            _issue(
                code,
                label,
                SEVERITY_WARN,
                affected,
                lambda row, kb=key_by_id, kn=key_name: (
                    f"{kn}: {kb.get(_row_id(row), '')}"
                ),
                hint="Слейте карточки в МойСклад — иначе рассылка уйдёт дважды.",
            )
        )
    return issues


def audit_catalog(catalog: dict[str, Any], *, today: date | None = None) -> dict[str, Any]:
    """Full data-quality report over the deduped catalog rows."""
    today = today or date.today()
    rows = [r for r in (catalog.get("rows") or []) if isinstance(r, dict)]

    no_name: list[dict[str, Any]] = []
    unreachable: list[dict[str, Any]] = []
    bad_phone: list[dict[str, Any]] = []
    orders_without_channel: list[dict[str, Any]] = []
    money_without_orders: list[dict[str, Any]] = []
    orders_without_money: list[dict[str, Any]] = []
    bad_birthdate: list[dict[str, Any]] = []
    future_order: list[dict[str, Any]] = []
    debt: list[dict[str, Any]] = []
    no_groups: list[dict[str, Any]] = []

    for row in rows:
        if not _row_name(row):
            no_name.append(row)
        if not row_has_phone(row) and not row_has_telegram(row):
            unreachable.append(row)

        raw_phone = str(row.get("Телефон") or row.get("phone") or "").strip()
        if raw_phone and len(_DIGITS.sub("", raw_phone)) and not normalize_phone(raw_phone):
            bad_phone.append(row)
        elif raw_phone:
            digits = normalize_phone(raw_phone)
            if digits and len(digits) != _PLAUSIBLE_PHONE_LEN:
                bad_phone.append(row)

        order_count = int(row.get("order_count") or row.get("Всего заказов") or 0)
        channels = unique_sales_channels(row)
        if order_count > 0 and not channels:
            orders_without_channel.append(row)

        avg_check = row.get("avg_check")
        paid = int(row.get("paid_order_count") or 0)
        if order_count <= 0 and avg_check not in (None, "", 0):
            money_without_orders.append(row)
        if paid > 0 and avg_check in (None, "", 0):
            orders_without_money.append(row)

        birth = row.get("Дата рождения") or row.get("birthdate")
        if str(birth or "").strip():
            parsed = _parse_date(birth)
            if parsed is None or parsed > today or parsed.year < 1900:
                bad_birthdate.append(row)

        last_order = _parse_date(row.get("last_order_at") or row.get("Дата последнего заказа"))
        if last_order is not None and last_order > today:
            future_order.append(row)

        balance = row.get("balance")
        try:
            if balance is not None and float(balance) < 0:
                debt.append(row)
        except (TypeError, ValueError):
            pass

        if not (row.get("_moysklad_tags") or []) and not (row.get("ai_groups") or []):
            no_groups.append(row)

    issues = [
        _issue(
            "no_name",
            "Карточка без наименования",
            SEVERITY_ERROR,
            no_name,
            lambda row: f"id {_row_id(row)}",
            hint="Без имени клиент не находится поиском и не персонализируется.",
        ),
        _issue(
            "unreachable",
            "Ни телефона, ни Telegram",
            SEVERITY_ERROR,
            unreachable,
            lambda row: f"заказов {int(row.get('order_count') or 0)}",
            hint="В рассылку такой клиент не попадёт никогда.",
        ),
        _issue(
            "bad_phone",
            "Телефон не похож на номер",
            SEVERITY_WARN,
            bad_phone,
            lambda row: str(row.get("Телефон") or row.get("phone") or ""),
            hint="Проверьте формат: лишние цифры, добавочные, склеенные номера.",
        ),
        _issue(
            "orders_without_channel",
            "Есть заказы, но нет канала продаж",
            SEVERITY_WARN,
            orders_without_channel,
            lambda row: f"заказов {int(row.get('order_count') or 0)}",
            hint="Такой клиент не попадает ни в Прямые, ни в Маркетплейс корректно.",
        ),
        _issue(
            "money_without_orders",
            "Средний чек есть, заказов ноль",
            SEVERITY_WARN,
            money_without_orders,
            lambda row: f"средний чек {row.get('avg_check')}",
            hint="Расхождение кэша и заказов — пересинхронизируйте.",
        ),
        _issue(
            "orders_without_money",
            "Оплаченные заказы есть, суммы нет",
            SEVERITY_WARN,
            orders_without_money,
            lambda row: f"оплачено заказов {int(row.get('paid_order_count') or 0)}",
            hint="Суммы заказов не доехали из МойСклад.",
        ),
        _issue(
            "bad_birthdate",
            "Дата рождения битая или в будущем",
            SEVERITY_WARN,
            bad_birthdate,
            lambda row: str(row.get("Дата рождения") or row.get("birthdate") or ""),
            hint="Фильтр «ДР / события» такого клиента не увидит.",
        ),
        _issue(
            "future_order",
            "Дата последнего заказа в будущем",
            SEVERITY_WARN,
            future_order,
            lambda row: str(row.get("last_order_at") or ""),
            hint="Скорее всего опечатка в дате заказа.",
        ),
        _issue(
            "debt",
            "Отрицательный баланс (долг)",
            SEVERITY_INFO,
            debt,
            lambda row: f"баланс {row.get('balance')}",
            hint="Проверьте перед любым апсейлом — рассылка про букеты тут неуместна.",
        ),
        _issue(
            "no_groups",
            "Нет ни одной группы (ни МойСклад, ни ИИ)",
            SEVERITY_INFO,
            no_groups,
            lambda row: f"заказов {int(row.get('order_count') or 0)}",
            hint="Такой клиент не ловится ни одним чипом групп.",
        ),
    ]
    issues.extend(_duplicate_issues(rows))
    issues = [i for i in issues if i["count"] > 0]
    issues.sort(
        key=lambda i: (
            {SEVERITY_ERROR: 0, SEVERITY_WARN: 1, SEVERITY_INFO: 2}.get(i["severity"], 3),
            -i["count"],
        )
    )

    stages: dict[str, int] = defaultdict(int)
    for row in rows:
        stages[row_client_stage(row)] += 1

    return {
        "ok": True,
        "checked_at": datetime.now().replace(microsecond=0).isoformat(),
        "rows_total": len(rows),
        "issues": issues,
        "issues_total": sum(i["count"] for i in issues),
        "errors_total": sum(i["count"] for i in issues if i["severity"] == SEVERITY_ERROR),
        "stages": dict(stages),
        "clean": not issues,
    }
