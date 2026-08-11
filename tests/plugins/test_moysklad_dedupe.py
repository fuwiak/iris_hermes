"""Multi-stage MoySklad client dedupe + pagination invariants."""

from __future__ import annotations

from plugins.moysklad.audience import row_matches_audience_extras
from plugins.moysklad.classify import clients_page
from plugins.moysklad.dedupe import (
    dedupe_by_canonical_id,
    dedupe_by_contact_keys,
    dedupe_by_fuzzy_name_phone,
    dedupe_catalog_rows,
    merge_catalogs,
    merge_rows,
    normalize_phone,
    normalize_telegram,
)


def _row(
    *,
    cid: str,
    name: str = "",
    phone: str = "",
    email: str = "",
    tg: str = "",
    orders: int = 0,
    tags: list[str] | None = None,
) -> dict:
    ctx = [{"id": f"o-{cid}-{i}", "sum": 1000.0, "moment": f"2026-0{i+1}-01"} for i in range(orders)]
    return {
        "_moysklad_id": cid,
        "Наименование": name,
        "Телефон": phone,
        "email": email,
        "E-mail": email,
        "ТГ ник": tg,
        "_moysklad_tags": list(tags or []),
        "_moysklad_tags_display": ", ".join(tags or []),
        "Группы": ", ".join(tags or []),
        "_orders_context": ctx,
        "order_count": orders,
        "Всего заказов": orders,
        "_audience": {"direct": True, "marketplace": False},
        "Тип канала продаж": "прямые продажи",
        "Канал продаж": "Telegram",
        "_order_channels_all": ["Telegram"],
    }


def test_stage1_canonical_id_merges_duplicates() -> None:
    rows = [
        _row(cid="a", name="Alice", phone="+79991112233", orders=1),
        _row(cid="a", name="Alice", phone="+79991112233", orders=2),
        _row(cid="b", name="Bob", phone="+79990001122", orders=0),
    ]
    out = dedupe_by_canonical_id(rows)
    assert len(out) == 2
    alice = next(r for r in out if r["_moysklad_id"] == "a")
    assert alice["order_count"] == 2


def test_stage2_contact_keys_merge_phone_and_telegram() -> None:
    rows = [
        _row(cid="1", name="Ann", phone="8 (999) 111-22-33", email="a@x.ru"),
        _row(cid="2", name="Ann TG", phone="+7 999 111 22 33", tg="@ann"),
        _row(cid="3", name="Other", tg="@ann"),
    ]
    out = dedupe_by_contact_keys(rows)
    assert len(out) == 1
    assert normalize_phone(out[0]["Телефон"]) == "9991112233"
    assert normalize_telegram(out[0]["ТГ ник"]) == "ann"


def test_stage3_fuzzy_name_phone() -> None:
    rows = [
        _row(cid="1", name="Иван Иванов", phone="79991234567"),
        _row(cid="2", name="иван  иванов", phone="89991234567"),
    ]
    out = dedupe_by_fuzzy_name_phone(rows)
    assert len(out) == 1


def test_stage4_cache_merge_update_in_place() -> None:
    existing = {
        "rows": [_row(cid="1", name="Old", phone="79991112233", orders=1)],
        "counts": {"total": 1},
        "orders_scanned": 10,
    }
    incoming = {
        "rows": [
            _row(cid="1", name="New", phone="79991112233", orders=3),
            _row(cid="2", name="Extra", phone="79990000000", orders=0),
        ],
        "counts": {"total": 2},
        "orders_scanned": 12,
    }
    merged = merge_catalogs(existing, incoming)
    assert len(merged["rows"]) == 2
    assert merged["counts"]["total"] == 2
    ids = [r["_moysklad_id"] for r in merged["rows"]]
    assert ids.count("1") == 1
    winner = next(r for r in merged["rows"] if r["_moysklad_id"] == "1")
    assert winner["order_count"] == 3
    assert merged["orders_scanned"] == 12


def test_full_pipeline_no_dup_ids() -> None:
    rows = [
        _row(cid="1", name="A", phone="79991111111"),
        _row(cid="1", name="A2", phone="79991111111"),
        _row(cid="2", name="B", phone="79991111111"),  # same phone → stage2
        _row(cid="3", name="C", email="c@x.ru"),
        _row(cid="4", name="C", email="c@x.ru"),
    ]
    out = dedupe_catalog_rows(rows)
    ids = [r["_moysklad_id"] for r in out]
    assert len(ids) == len(set(ids))
    assert len(out) == 2


def test_pagination_no_dup_ids_across_pages() -> None:
    catalog = {
        "rows": [
            _row(
                cid=str(i),
                name=f"Client {i}",
                phone=f"7999000{i:04d}",
                tags=["8 марта"] if i % 5 == 0 else [],
            )
            for i in range(120)
        ],
        "counts": {"direct": 120, "marketplace": 0, "other": 0, "total": 120},
        "orders_scanned": 0,
        "counterparties_scanned": 120,
        "counterparties_deduped": 120,
    }
    # Force dedupe path on merge_rows so catalog is clean.
    catalog["rows"] = merge_rows([], catalog["rows"])

    class _Dummy:
        pass

    seen: set[str] = set()
    offset = 0
    limit = 50
    total = None
    while True:
        page = clients_page(
            _Dummy(),  # type: ignore[arg-type]
            sales_filter="all",
            limit=limit,
            offset=offset,
            catalog=catalog,
        )
        total = page["matched_total"]
        ids = [c["id"] for c in page["clients"]]
        assert len(ids) == len(set(ids))
        overlap = seen.intersection(ids)
        assert not overlap, f"dup across pages: {overlap}"
        seen.update(ids)
        if not page["has_more"]:
            break
        offset = page["next_offset"]
    assert total == 120
    assert len(seen) == 120


def test_audience_channel_and_tag_filters() -> None:
    tg = _row(cid="1", name="TG", tg="@user", phone="")
    wa = _row(cid="2", name="WA", phone="79991234567", tg="")
    vip = _row(cid="3", name="VIP", phone="79991111111", tags=["VIP", "8 марта"])
    bday = _row(cid="4", name="Bday", phone="79992222222", tags=["событие марта"])

    assert row_matches_audience_extras(tg, channel_kind="telegram")
    assert not row_matches_audience_extras(wa, channel_kind="telegram", require_telegram=True)
    assert row_matches_audience_extras(wa, channel_kind="whatsapp")
    assert row_matches_audience_extras(vip, vip_only=True, group="8 марта")
    assert row_matches_audience_extras(bday, birthday_soon=True)
    assert not row_matches_audience_extras(wa, birthday_soon=True)


def test_row_matches_query_phone_normalize() -> None:
    from plugins.moysklad.classify import _row_matches_query

    row = _row(cid="p1", name="Саша", phone="+7 (919) 787-51-13")
    assert _row_matches_query(row, "+79197875113")
    assert _row_matches_query(row, "79197875113")
    assert _row_matches_query(row, "9197875113")
    assert _row_matches_query(row, "саша")
    assert not _row_matches_query(row, "0000000000")


def test_clients_page_search_spans_sales_tabs() -> None:
    """Search must find marketplace clients even when UI tab is «direct»."""
    from plugins.moysklad.sales_channels import refresh_row_channel_fields

    direct = _row(cid="d1", name="Прямой", phone="+79991112233", orders=1)
    direct["_orders_context"] = [
        {"id": "o1", "Канал продаж": "Telegram", "channel": "Telegram", "sum": 1000}
    ]
    refresh_row_channel_fields(direct)

    mp = _row(cid="m1", name="Маркет", phone="+7 (919) 787-51-13", orders=1)
    mp["_orders_context"] = [
        {
            "id": "o2",
            "Канал продаж": "FlowWow Skyloft",
            "channel": "FlowWow Skyloft",
            "sum": 2000,
        }
    ]
    refresh_row_channel_fields(mp)

    catalog = {
        "rows": [direct, mp],
        "counts": {"total": 2, "direct": 1, "marketplace": 1},
        "orders_scanned": 2,
        "counterparties_scanned": 2,
        "counterparties_deduped": 2,
    }

    class _Dummy:
        pass

    page = clients_page(
        _Dummy(),  # type: ignore[arg-type]
        sales_filter="direct",
        q="79197875113",
        catalog=catalog,
    )
    ids = {c["id"] for c in page["clients"]}
    assert "m1" in ids
    assert page["matched_total"] >= 1
