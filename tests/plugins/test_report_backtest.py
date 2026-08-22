"""Backtest of the Вереск report: extraction, holdout compare, overrides."""

from __future__ import annotations

from datetime import date

from plugins.moysklad.dashboard_analytics import build_analytics
from plugins.moysklad.report_backtest import (
    apply_overrides,
    compare_reports,
    extract_month_report,
    reference_template,
)


def _paid(oid: str, moment: str, amount: float, channel: str, client: str) -> dict:
    return {
        "id": client,
        "_orders_context": [
            {
                "id": oid,
                "moment": moment,
                "sum": amount,
                "channel": channel,
                "Канал продаж": channel,
                "payment_status": "paid",
                "payed_sum": amount,
                "unpaid": 0,
            }
        ],
    }


def _computed() -> dict:
    rows = [
        _paid("o1", "2026-08-03T10:00:00", 1000, "Яндекс Маркет", "c1"),
        _paid("o2", "2026-08-04T10:00:00", 2000, "Яндекс Маркет", "c2"),
        _paid("o3", "2026-07-15T10:00:00", 5000, "Telegram", "c4"),
    ]
    analytics = build_analytics(
        rows, today=date(2026, 8, 12), day_limit=14, week_limit=6, month_limit=4
    )
    return extract_month_report(analytics)


def test_extract_month_report_pivots_series() -> None:
    computed = _computed()
    aug = computed["2026-08"]["yandex_market"]
    assert aug["orders"] == 2
    assert aug["turnover"] == 3000.0
    # revenue = turnover * (1 - 0.30) — the Excel «minus 30%» rule
    assert aug["revenue"] == 2100.0


def test_holdout_match_within_tolerance() -> None:
    computed = _computed()
    reference = {"2026-08": {"yandex_market": {"turnover": 3010, "orders": 2}}}
    result = compare_reports(computed, reference, months=["2026-08"], tolerance=0.01)
    assert result["verdict"] == "match"
    assert not result["mismatches"] and not result["missing_data"]


def test_holdout_mismatch_surfaces_delta() -> None:
    computed = _computed()
    reference = {"2026-08": {"yandex_market": {"turnover": 5000}}}
    result = compare_reports(computed, reference, months=["2026-08"], tolerance=0.01)
    assert result["verdict"] == "mismatch"
    row = result["mismatches"][0]
    assert row["metric"] == "turnover"
    assert row["delta"] == -2000.0


def test_missing_marketplace_data_reported_then_filled() -> None:
    computed = _computed()
    # Reference knows Flowwow July turnover; MoySklad rows have none.
    reference = {"2026-07": {"flowwow": {"turnover": 40000}, "direct": {"turnover": 5000}}}
    result = compare_reports(computed, reference, months=["2026-07"], tolerance=0.01)
    assert result["verdict"] == "needs_data"
    missing = result["missing_data"][0]
    assert missing["channel"] == "flowwow"
    assert "маркетплейса" in missing["reason"]

    # «вот тебе недостающие цифры» — fill and re-compare.
    notes = apply_overrides(computed, {"2026-07": {"flowwow": {"turnover": 40000}}})
    assert notes
    result2 = compare_reports(computed, reference, months=["2026-07"], tolerance=0.01)
    assert result2["verdict"] == "match"


def test_reference_template_round_trips() -> None:
    computed = _computed()
    template = reference_template(computed, months=["2026-08"])
    ym = template["months"]["2026-08"]["yandex_market"]
    assert ym["turnover"] == 3000.0
    result = compare_reports(computed, template["months"], tolerance=0.0)
    assert result["verdict"] == "match"
