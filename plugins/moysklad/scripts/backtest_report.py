#!/usr/bin/env python3
"""Backtest the «аналитика Вереск» monthly report against reference numbers.

Client's verification loop (call 22.08.2026): hold out months from the
reference report, rebuild them from MoySklad data only, list the figures
MoySklad cannot provide, accept manual fills, diff, reconcile.

Usage (repo root, MOYSKLAD_API_TOKEN in .env):

  # 1. Build the report and write a reference skeleton to fill with the
  #    client's Excel numbers (or hand to the client):
  .venv/bin/python3 plugins/moysklad/scripts/backtest_report.py --emit-template ref.json

  # 2. Compare held-out months against the (edited) reference:
  .venv/bin/python3 plugins/moysklad/scripts/backtest_report.py \
      --reference ref.json --months 2026-07,2026-08 --tolerance 0.02

  # 3. Same, with manually supplied figures MoySklad does not have:
  .venv/bin/python3 plugins/moysklad/scripts/backtest_report.py \
      --reference ref.json --months 2026-07 --fill missing.json \
      --dump-computed computed.json

Exit codes: 0 match, 2 missing data (needs marketplace figures), 1 mismatch.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_dotenv() -> None:
    import os

    for candidate in (REPO_ROOT / ".env", Path.home() / ".hermes" / ".env"):
        if not candidate.is_file():
            continue
        for line in candidate.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def _load_rows(max_orders: int, max_counterparties: int) -> list[dict[str, Any]]:
    from plugins.moysklad.catalog_cache import cache_key, get_cached
    from plugins.moysklad.classify import build_enriched_catalog
    from plugins.moysklad.client import MoySkladClient

    key = cache_key(
        max_orders=max_orders,
        max_counterparties=max_counterparties,
        include_archived=False,
    )
    cached = get_cached(key)
    catalog = cached.get("catalog") if isinstance(cached, dict) else None
    if not (isinstance(catalog, dict) and catalog.get("rows")):
        print("… cache empty, fetching MoySklad (slow)", flush=True)
        catalog = build_enriched_catalog(
            MoySkladClient(),
            max_orders=max_orders,
            max_counterparties=max_counterparties,
        )
    rows = list(catalog.get("rows") or [])
    print(f"… catalog ready · {len(rows)} clients", flush=True)
    return rows


def _fmt_row(row: dict[str, Any]) -> str:
    base = f"{row.get('month')} · {row.get('channel') or '—'} · {row.get('metric') or '—'}"
    if "computed" in row:
        base += f": расчёт {row.get('computed')} vs образец {row.get('reference')}"
        if row.get("delta") is not None:
            base += f" (Δ {row['delta']}, {round(100 * float(row.get('relative') or 0), 1)}%)"
    if row.get("reason"):
        base += f" — {row['reason']}"
    return base


def main() -> int:
    _load_dotenv()
    parser = argparse.ArgumentParser(description="Backtest the Вереск monthly report")
    parser.add_argument("--reference", help="Reference JSON ({'months': {...}}) to compare against")
    parser.add_argument("--months", help="Comma-separated held-out months (2026-07,2026-08)")
    parser.add_argument("--fill", help="JSON with manually supplied figures (same shape as months)")
    parser.add_argument("--tolerance", type=float, default=0.01, help="Relative tolerance (default 1%%)")
    parser.add_argument("--emit-template", help="Write a reference skeleton from computed data and exit")
    parser.add_argument("--dump-computed", help="Write the computed month report to this JSON path")
    parser.add_argument("--month-limit", type=int, default=14, help="Months of history to compute")
    parser.add_argument("--max-orders", type=int, default=8000)
    parser.add_argument("--max-counterparties", type=int, default=4000)
    args = parser.parse_args()

    if not args.reference and not args.emit_template:
        parser.error("either --reference or --emit-template is required")

    from plugins.moysklad.dashboard_analytics import build_analytics
    from plugins.moysklad.report_backtest import (
        apply_overrides,
        compare_reports,
        extract_month_report,
        reference_template,
    )

    rows = _load_rows(args.max_orders, args.max_counterparties)
    analytics = build_analytics(rows, today=date.today(), month_limit=args.month_limit)
    computed = extract_month_report(analytics)
    months = [m.strip() for m in (args.months or "").split(",") if m.strip()] or None

    if args.emit_template:
        template = reference_template(computed, months=months)
        Path(args.emit_template).write_text(
            json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"template → {args.emit_template} ({len(template['months'])} months)")
        return 0

    if args.fill:
        overrides = json.loads(Path(args.fill).read_text(encoding="utf-8"))
        notes = apply_overrides(computed, overrides.get("months") or overrides)
        for note in notes:
            print(f"  fill: {note}")

    if args.dump_computed:
        Path(args.dump_computed).write_text(
            json.dumps({"months": computed}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"computed → {args.dump_computed}")

    reference = json.loads(Path(args.reference).read_text(encoding="utf-8"))
    result = compare_reports(
        computed,
        reference.get("months") or {},
        months=months,
        tolerance=args.tolerance,
    )

    print(
        f"\nМесяцы: {', '.join(result['months'])} · допуск {args.tolerance:.0%} · "
        f"ok={len(result['ok'])} mismatch={len(result['mismatches'])} "
        f"missing={len(result['missing_data'])}"
    )
    if result["missing_data"]:
        print("\nНе хватает данных (взять из кабинета маркетплейса и подать через --fill):")
        for row in result["missing_data"]:
            print("  ✗", _fmt_row(row))
    if result["mismatches"]:
        print("\nРасхождения (разобраться, почему возникли):")
        for row in result["mismatches"]:
            print("  ✗", _fmt_row(row))
    if result["verdict"] == "match":
        print("\n✓ Отчёт совпадает с образцом в пределах допуска.")
        return 0
    return 2 if result["verdict"] == "needs_data" else 1


if __name__ == "__main__":
    raise SystemExit(main())
