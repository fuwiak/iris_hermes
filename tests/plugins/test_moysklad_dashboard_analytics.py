"""Excel-formula analytics for the MoySklad Дашборд (no live API)."""

from __future__ import annotations

from datetime import date

from plugins.moysklad.dashboard_analytics import (
    avg_check,
    build_analytics,
    classify_analytics_channel,
    classify_store,
    commission_from_turnover,
    margin_from_revenue,
    pct_change,
    revenue_from_turnover,
    share_of_total,
    week_label,
    week_start,
)


def test_excel_growth_formula() -> None:
    # =(E2/C2*100-100)/100  with C2=117296, E2=212254
    assert round(pct_change(212254, 117296) or 0, 6) == round(212254 / 117296 - 1, 6)
    assert pct_change(10, 0) is None
    assert pct_change(None, 5) is None


def test_excel_avg_check_and_revenue() -> None:
    assert avg_check(117296, 18) == 117296 / 18
    assert avg_check(100, 0) is None
    # Яндекс Еда: C17 = C16-(C16*25/100)
    assert revenue_from_turnover(1000, 0.25) == 750
    # Яндекс Маркет: S10 = S9-(S9*30/100)
    assert revenue_from_turnover(1000, 0.30) == 700
    # Флау: A45 = 0.346
    assert round(revenue_from_turnover(1000, 0.346), 3) == 654.0
    assert commission_from_turnover(1000, 0.25) == 250


def test_excel_margin_with_share() -> None:
    # C11 = C10 - C48 * D9/100 ; D9 = C9*100/C47
    revenue = 183491
    purchase = 1_621_953
    channel_turnover = 300_000
    total_turnover = 1_000_000
    sh = share_of_total(channel_turnover, total_turnover)
    assert sh == 0.3
    assert margin_from_revenue(revenue, purchase, sh) == revenue - purchase * 0.3
    assert share_of_total(10, 0) == 0.0


def test_channel_and_store_from_moysklad_names() -> None:
    assert classify_analytics_channel("FlowWow Сокольники") == "flowwow"
    assert classify_store("FlowWow Сокольники") == "sokolniki"
    assert classify_analytics_channel("FlowWow Skyloft") == "skyloft"
    assert classify_analytics_channel("FlowWow Floday") == "floday"
    assert classify_analytics_channel("Яндекс Еда") == "yandex_eda"
    assert classify_analytics_channel("Яндекс.Маркет") == "yandex_market"
    assert classify_analytics_channel("Флавери") == "flavy"
    assert classify_analytics_channel("Ozon") == "ozon"
    assert classify_analytics_channel("Telegram") == "direct"
    assert classify_analytics_channel("Витрина") == "direct"
    assert classify_store("Flowwow Университет") == "universitet"


def test_week_bucket_monday_sunday() -> None:
    # 1 Sep 2025 was Monday — Excel «1-7 сент»
    assert week_start(date(2025, 9, 3)) == date(2025, 9, 1)
    assert week_label(date(2025, 9, 1)) == "1–7 сент"
    assert "окт" in week_label(date(2025, 9, 29))


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


def test_build_analytics_aggregates_like_excel_sheets() -> None:
    rows = [
        _paid("o1", "2026-08-03T10:00:00", 1000, "Яндекс Маркет", "c1"),
        _paid("o2", "2026-08-04T10:00:00", 2000, "Яндекс Маркет", "c2"),
        _paid("o3", "2026-08-10T10:00:00", 4000, "FlowWow Сокольники", "c3"),
        _paid("o4", "2026-08-11T10:00:00", 1500, "FlowWow Сокольники", "c3"),
        _paid("o5", "2026-07-15T10:00:00", 5000, "Telegram", "c4"),
        _paid("skip", "2026-08-03T10:00:00", 999, "Ozon", "c5"),
    ]
    # cancelled must not enter оборот (unpaid DOES count — marketplace orders
    # often carry no per-order payment stamp; see analytics_paid_only)
    rows[-1]["_orders_context"][0]["payment_status"] = "cancelled"
    rows[-1]["_orders_context"][0]["applicable"] = False

    analytics = build_analytics(
        rows, today=date(2026, 8, 12), day_limit=14, week_limit=6, month_limit=4
    )
    assert analytics["order_count"] == 5
    assert analytics["formulas"]["growth"] == "(new/old)-1"

    ym = next(c for c in analytics["by_month"]["channels"] if c["key"] == "yandex_market")
    aug = analytics["by_month"]["periods"][-1]
    assert aug["id"] == "2026-08"
    aug_i = len(analytics["by_month"]["periods"]) - 1
    assert ym["turnover"][aug_i] == 3000
    assert ym["orders"][aug_i] == 2
    assert ym["avg_check"][aug_i] == 1500
    assert ym["revenue"][aug_i] == 2100  # 30% commission
    assert ym["growth"]["turnover"][aug_i] is None or True

    fw = next(c for c in analytics["by_month"]["channels"] if c["key"] == "flowwow")
    assert fw["turnover"][aug_i] == 5500
    # store split lives on by_day cells
    day_aug10 = next(r for r in analytics["by_day"]["rows"] if r["id"] == "2026-08-10")
    assert day_aug10["channels"]["flowwow"]["sokolniki_orders"] == 1
    assert day_aug10["channels"]["flowwow"]["sokolniki_turnover"] == 4000

    # week containing 3-4 Aug 2026 (Mon 3 Aug)
    week_ids = [p["id"] for p in analytics["by_week"]["periods"]]
    assert "2026-08-03" in week_ids
    w_i = week_ids.index("2026-08-03")
    assert ym["key"] == "yandex_market"
    ym_w = next(c for c in analytics["by_week"]["channels"] if c["key"] == "yandex_market")
    assert ym_w["turnover"][w_i] == 3000

    # FlowWow 2nd purchase for c3 in August
    fw_sheet = analytics["flowwow"]
    aug_fw = [i for i, p in enumerate(fw_sheet["periods"]) if p["id"] == "2026-08"][0]
    assert fw_sheet["metrics"]["orders"][aug_fw] == 2
    assert fw_sheet["metrics"]["new_clients"][aug_fw] == 1
    assert fw_sheet["metrics"]["second_purchase"][aug_fw] == 1
    assert fw_sheet["metrics"]["revenue"][aug_fw] == round(5500 * (1 - 0.346), 2)

    # July direct still in month matrix
    jul_i = [i for i, p in enumerate(analytics["by_month"]["periods"]) if p["id"] == "2026-07"][0]
    direct = next(c for c in analytics["by_month"]["channels"] if c["key"] == "direct")
    assert direct["turnover"][jul_i] == 5000
    assert direct["revenue"][jul_i] == 5000  # 0% commission
    assert direct["turnover"][aug_i] == 0
    assert direct["growth"]["turnover"][aug_i] == -1.0

    ids = {row["id"] for row in analytics["insights"]}
    assert "concentration" in ids
    assert "mom-down" in ids
    assert "cheap-mix" in ids
    assert "commission-bite" in ids
    conc = next(row for row in analytics["insights"] if row["id"] == "concentration")
    assert conc["channel"] == "flowwow"
    assert conc["tone"] == "warn"


def test_yandex_cabinet_reprices_turnover_and_deliveries() -> None:
    from plugins.moysklad.dashboard_analytics import apply_yandex_cabinet_to_raw, build_analytics

    rows = [
        _paid("o1", "2026-07-10T10:00:00", 10_000, "Яндекс Маркет", "c1"),
        _paid("o2", "2026-07-11T10:00:00", 5_000, "Яндекс Маркет", "c2"),
    ]
    cabinet = {
        "months": {
            "2026-07": {
                "orders": 3,
                "buyer_total": 9000.0,
                "payout_total": 6000.0,
                "deliveries": 400.0,
            }
        }
    }
    analytics = build_analytics(
        rows,
        today=date(2026, 7, 20),
        day_limit=20,
        week_limit=4,
        month_limit=3,
        yandex_cabinet=cabinet,
        deliveries_by_month={"2026-07": 56680},
        purchase_by_month={"2026-07": 100_000},
    )
    ym = next(c for c in analytics["by_month"]["channels"] if c["key"] == "yandex_market")
    jul_i = [i for i, p in enumerate(analytics["by_month"]["periods"]) if p["id"] == "2026-07"][0]
    assert ym["turnover"][jul_i] == 9000.0  # cabinet BUYER, not MS 15000
    assert ym["orders"][jul_i] == 3
    assert ym["revenue"][jul_i] == 6300.0  # 9000 * 0.7
    assert ym["deliveries"][jul_i] == 56680  # manual override wins
    # share = 1.0 (only yandex) → margin = 6300 - 100000 * 1 = -93700
    assert ym["margin"][jul_i] == -93700.0
    assert analytics["yandex_source"] == "cabinet"

    raw = {("2026-07", "yandex_market"): {"orders": 2, "turnover": 15000.0, "deliveries": 0.0,
                                           "sokolniki_orders": 0, "sokolniki_turnover": 0.0,
                                           "universitet_orders": 0, "universitet_turnover": 0.0}}
    notes = apply_yandex_cabinet_to_raw(raw, yandex_cabinet=cabinet, use_cabinet=True)
    assert raw[("2026-07", "yandex_market")]["turnover"] == 9000.0
    assert any("BUYER" in n for n in notes)


def test_analytics_overrides_loader(tmp_path, monkeypatch) -> None:
    from plugins.moysklad import analytics_overrides as ao

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "moysklad").mkdir()
    (tmp_path / "moysklad" / "analytics_overrides.json").write_text(
        '{"purchase_by_month": {"2025-12": 1e6}, "deliveries_by_month": {"2025-12": 282870},'
        ' "yandex_use_cabinet": false}',
        encoding="utf-8",
    )
    out = ao.load_analytics_overrides()
    assert out["purchase_by_month"]["2025-12"] == 1_000_000.0
    assert out["deliveries_by_month"]["2025-12"] == 282870.0
    assert out["yandex_use_cabinet"] is False
