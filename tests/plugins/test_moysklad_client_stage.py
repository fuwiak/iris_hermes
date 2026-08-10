"""«Тип клиента» — кто состоялся, кто нет."""

from __future__ import annotations

from plugins.moysklad.audience import (
    normalize_stage_filter,
    row_client_stage,
    row_matches_stage,
    stage_counts,
)
from plugins.moysklad.order_status import (
    STAGE_CUSTOMER,
    STAGE_FAILED,
    STAGE_NO_ORDERS,
    client_stage,
    client_stage_reason,
    summarize_order_context,
)


def _order(sum_rub: float, payed: float | None, *, moment: str = "2026-01-10T10:00:00"):
    """One row of ``_orders_context`` as ``classify`` builds it (rubles)."""
    order: dict = {"sum": sum_rub, "moment": moment}
    if payed is not None:
        order["payed_sum"] = payed
        order["unpaid"] = round(max(0.0, sum_rub - payed), 2)
    return order


def test_stage_from_outcome():
    assert client_stage("customer") == STAGE_CUSTOMER
    assert client_stage("failed") == STAGE_FAILED
    # Fresh unpaid order — still никогда не покупал.
    assert client_stage("pending_payment") == STAGE_FAILED
    assert client_stage("none") == STAGE_NO_ORDERS


def test_paid_order_makes_a_customer():
    payment = summarize_order_context([_order(5000, 5000)])
    assert payment["customer_outcome"] == "customer"
    assert client_stage(payment["customer_outcome"]) == STAGE_CUSTOMER
    assert "оплаченных заказов: 1" in client_stage_reason(payment)


def test_orders_without_payment_are_failed():
    from datetime import date

    payment = summarize_order_context(
        [_order(5000, 0, moment="2020-01-10T10:00:00")], today=date(2026, 8, 10)
    )
    assert payment["customer_outcome"] == "failed"
    assert client_stage(payment["customer_outcome"]) == STAGE_FAILED
    assert "оплат нет" in client_stage_reason(payment)


def test_no_orders_is_its_own_bucket():
    payment = summarize_order_context([])
    assert client_stage(payment["customer_outcome"]) == STAGE_NO_ORDERS


def test_row_stage_falls_back_to_outcome_on_old_cache():
    # Catalog cached before «Тип клиента» existed — no stage field on the row.
    assert row_client_stage({"customer_outcome": "failed"}) == STAGE_FAILED
    assert row_client_stage({"client_stage": STAGE_CUSTOMER}) == STAGE_CUSTOMER


def test_stage_filter_matches_and_counts():
    rows = [
        {"client_stage": STAGE_FAILED},
        {"client_stage": STAGE_FAILED},
        {"client_stage": STAGE_CUSTOMER},
        {"customer_outcome": "none"},
    ]
    assert [row_matches_stage(r, "failed") for r in rows] == [True, True, False, False]
    assert [row_matches_stage(r, "all") for r in rows] == [True] * 4

    counts = stage_counts(rows)
    assert counts["failed"] == 2
    assert counts["customer"] == 1
    assert counts["no_orders"] == 1
    assert counts["all"] == 4


def test_stage_filter_key_aliases():
    assert normalize_stage_filter("не состоялся") == "failed"
    assert normalize_stage_filter("") == "all"
    assert normalize_stage_filter("мусор") == "all"
