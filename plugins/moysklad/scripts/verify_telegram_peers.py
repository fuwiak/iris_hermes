#!/usr/bin/env python3
"""Bulk-check Telegram reachability for MoySklad CRM clients.

Writes results to ``tg_verify_overlay.json`` (Redis when configured).
After the run, «TG активен» column + фильтр «Telegram» in Рассылки use the cache.

Usage (repo root, MOYSKLAD_API_TOKEN + Telegram user session configured):

  python plugins/moysklad/scripts/verify_telegram_peers.py
  python plugins/moysklad/scripts/verify_telegram_peers.py --limit 200 --delay-ms 400
  python plugins/moysklad/scripts/verify_telegram_peers.py --only-unchecked
  python plugins/moysklad/scripts/verify_telegram_peers.py --sales-filter direct

Requires personal Telegram (Telethon) or Business bot preflight — same as
«Проверить» in the client card.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_dotenv() -> None:
    candidates = [
        Path(os.environ.get("HERMES_HOME", "")).expanduser() / ".env"
        if os.environ.get("HERMES_HOME")
        else None,
        Path.home() / ".hermes" / ".env",
    ]
    for path in candidates:
        if path is None or not path.is_file():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                if key and key not in os.environ:
                    os.environ[key] = val.strip().strip('"').strip("'")
        except OSError:
            continue


def _load_catalog(max_orders: int, max_counterparties: int) -> dict[str, Any]:
    from plugins.moysklad.catalog_cache import cache_key, get_cached
    from plugins.moysklad.classify import build_enriched_catalog
    from plugins.moysklad.client import MoySkladClient
    from plugins.moysklad.tg_verify import stamp_catalog_rows_from_verify

    key = cache_key(
        max_orders=max_orders,
        max_counterparties=max_counterparties,
        include_archived=False,
    )
    cached = get_cached(key)
    catalog = cached.get("catalog") if isinstance(cached, dict) else None
    if not isinstance(catalog, dict) or not catalog.get("rows"):
        client = MoySkladClient()
        catalog = build_enriched_catalog(
            client,
            max_orders=max_orders,
            max_counterparties=max_counterparties,
        )
    rows = list(catalog.get("rows") or [])
    stamp_catalog_rows_from_verify(rows)
    catalog["rows"] = rows
    return catalog


def main() -> int:
    _load_dotenv()
    parser = argparse.ArgumentParser(description="Verify Telegram peers for MoySklad clients")
    parser.add_argument("--limit", type=int, default=0, help="Max clients to check (0 = all)")
    parser.add_argument("--delay-ms", type=int, default=350, help="Pause between MTProto checks")
    parser.add_argument(
        "--only-unchecked",
        action="store_true",
        help="Skip rows already present in tg_verify overlay",
    )
    parser.add_argument(
        "--sales-filter",
        default="all",
        choices=("all", "direct", "marketplace"),
        help="Restrict to sales tab before checking",
    )
    parser.add_argument("--max-orders", type=int, default=25000)
    parser.add_argument("--max-counterparties", type=int, default=0)
    args = parser.parse_args()

    from plugins.moysklad.sales_channels import row_matches_sales_filter
    from plugins.moysklad.tg_verify import (
        overlay_for_client,
        row_has_contact_for_tg_check,
        stamp_catalog_rows_from_verify,
        verify_catalog_row,
    )

    catalog = _load_catalog(args.max_orders, args.max_counterparties)
    rows = list(catalog.get("rows") or [])
    candidates = [
        r
        for r in rows
        if isinstance(r, dict)
        and row_has_contact_for_tg_check(r)
        and row_matches_sales_filter(r, args.sales_filter)
    ]
    if args.only_unchecked:
        filtered = []
        for row in candidates:
            cid = str(row.get("_moysklad_id") or row.get("id") or "").strip()
            if cid and overlay_for_client(cid):
                continue
            filtered.append(row)
        candidates = filtered

    cap = int(args.limit or 0)
    if cap > 0:
        candidates = candidates[:cap]

    total = len(candidates)
    if not total:
        print("Nothing to verify (no rows with @nick / chat id / phone).")
        return 0

    active = inactive = skipped = 0
    delay = max(0, int(args.delay_ms)) / 1000.0
    print(f"Checking {total} client(s)… (delay {args.delay_ms}ms)")

    for i, row in enumerate(candidates, start=1):
        cid = str(row.get("_moysklad_id") or row.get("id") or "").strip()
        name = str(row.get("Наименование") or row.get("name") or cid)[:48]
        nick = str(row.get("ТГ ник") or row.get("tg_nick") or "").strip()
        try:
            result = verify_catalog_row(row)
        except Exception as exc:
            skipped += 1
            print(f"[{i}/{total}] {name}: ERROR {exc}")
            continue
        if not result.get("checked"):
            skipped += 1
            print(f"[{i}/{total}] {name}: skip — {result.get('detail')}")
            continue
        if result.get("active"):
            active += 1
            resolved = result.get("resolved_nick") or nick
            print(f"[{i}/{total}] {name}: OK {resolved} via {result.get('via') or '?'}")
        else:
            inactive += 1
            print(f"[{i}/{total}] {name}: FAIL — {result.get('detail') or 'not found'}")
        if delay and i < total:
            time.sleep(delay)

    stamped = stamp_catalog_rows_from_verify(rows)
    print(
        f"Done. active={active} inactive={inactive} skipped={skipped} "
        f"stamped_rows={stamped}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
