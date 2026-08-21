#!/usr/bin/env python3
"""Bulk-check Telegram reachability for MoySklad CRM clients.

Writes results to ``tg_verify_overlay.json`` (Redis when configured).
After the run, «TG активен» column + фильтр «Telegram» in Рассылки use the cache.

Usage (from repo root; ``python`` may be missing — use venv):

  .venv/bin/python3 -u plugins/moysklad/scripts/verify_telegram_peers.py --only-unchecked --delay-ms 400

``-u`` = unbuffered so progress lines show immediately.
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

# Line-buffer even without ``python -u`` / a TTY.
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass


def _log(msg: str) -> None:
    print(msg, flush=True)


def _fmt_dur(seconds: float) -> str:
    sec = max(0, int(seconds))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


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

    _log("… loading catalog cache (can take 1–2 min, not frozen)")
    t0 = time.time()
    key = cache_key(
        max_orders=max_orders,
        max_counterparties=max_counterparties,
        include_archived=False,
    )
    cached = get_cached(key)
    catalog = cached.get("catalog") if isinstance(cached, dict) else None
    if isinstance(catalog, dict) and catalog.get("rows"):
        _log(f"… cache hit · {len(catalog['rows'])} rows · {_fmt_dur(time.time() - t0)}")
    else:
        _log("… cache empty, fetching MoySklad (slow)")
        client = MoySkladClient()
        catalog = build_enriched_catalog(
            client,
            max_orders=max_orders,
            max_counterparties=max_counterparties,
        )
        _log(
            f"… MoySklad fetch done · {len(catalog.get('rows') or [])} rows · "
            f"{_fmt_dur(time.time() - t0)}"
        )
    rows = list(catalog.get("rows") or [])
    try:
        from plugins.moysklad.telegram_export import stamp_catalog_rows_from_overlay

        _log("… stamping Telegram export overlay")
        stamp_catalog_rows_from_overlay(rows)
    except Exception:
        pass
    stamp_catalog_rows_from_verify(rows)
    catalog["rows"] = rows
    _log(f"… catalog ready · {len(rows)} clients")
    return catalog


def main() -> int:
    _log("verify_telegram_peers: start")
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
        "--nicks-only",
        action="store_true",
        help="Only clients with ТГ ник (legacy; default is колонка Телефон)",
    )
    parser.add_argument(
        "--cache-only",
        action="store_true",
        help="Only match catalog phones to Telegram contacts cache (no live ImportContacts)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop previous tg_verify overlay before this run",
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

    from plugins.moysklad.conversations import normalize_tg_nick
    from plugins.moysklad.sales_channels import row_matches_sales_filter
    from plugins.moysklad.tg_verify import (
        match_catalog_phones_to_contacts,
        overlay_for_client,
        row_has_contact_for_tg_check,
        save_overlay,
        stamp_catalog_rows_from_verify,
        verify_catalog_row,
    )

    if args.reset:
        save_overlay({"by_client_id": {}, "stats": {}})
        _log("Reset tg_verify overlay.")

    catalog = _load_catalog(args.max_orders, args.max_counterparties)
    rows = list(catalog.get("rows") or [])

    _log("… matching catalog phones to Telegram contacts cache")
    cache_stats = match_catalog_phones_to_contacts(rows)
    _log(
        "Contacts cache: "
        f"catalog phones={cache_stats.get('scanned', 0)} "
        f"contacts_with_phone={cache_stats.get('contacts_with_phone', 0)} "
        f"matched={cache_stats.get('matched', 0)}"
    )
    stamp_catalog_rows_from_verify(rows)

    if args.cache_only:
        _log("Cache-only — skip live phone resolve.")
        return 0

    def _has_nick(row: dict[str, Any]) -> bool:
        return bool(normalize_tg_nick(str(row.get("ТГ ник") or row.get("tg_nick") or "")))

    candidates = [
        r
        for r in rows
        if isinstance(r, dict)
        and row_has_contact_for_tg_check(r)
        and row_matches_sales_filter(r, args.sales_filter)
        and (not args.nicks_only or _has_nick(r))
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
        _log("Nothing to verify (no rows with phone in колонка Телефон).")
        return 0

    active = inactive = skipped = 0
    delay = max(0, int(args.delay_ms)) / 1000.0
    eta_note = ""
    if delay:
        eta_note = f" · ETA ≥ {_fmt_dur(total * (delay + 0.3))}"
    _log(f"Checking {total} phone(s)… delay {args.delay_ms}ms{eta_note}")
    t_loop = time.time()

    for i, row in enumerate(candidates, start=1):
        cid = str(row.get("_moysklad_id") or row.get("id") or "").strip()
        name = str(row.get("Наименование") or row.get("name") or cid)[:40]
        phone = str(row.get("Телефон") or row.get("phone") or "").strip()
        pct = (100.0 * i / total) if total else 0.0
        elapsed = time.time() - t_loop
        eta = ""
        if i > 1:
            per = elapsed / (i - 1)
            eta = f" · ETA {_fmt_dur(per * (total - i + 1))}"
        _log(f"→ [{i}/{total} {pct:5.1f}%] {name} {phone} …{eta}")
        try:
            result = verify_catalog_row(row)
        except Exception as exc:
            skipped += 1
            _log(f"  ✗ ERROR {exc}  (ok={active} fail={inactive} skip={skipped})")
            continue
        if not result.get("checked"):
            skipped += 1
            _log(
                f"  · skip — {result.get('detail')}  "
                f"(ok={active} fail={inactive} skip={skipped})"
            )
            continue
        if result.get("active"):
            active += 1
            resolved = result.get("resolved_nick") or ""
            _log(
                f"  ✓ OK {resolved} via {result.get('via') or '?'}  "
                f"(ok={active} fail={inactive} skip={skipped})"
            )
        else:
            inactive += 1
            _log(
                f"  ✗ FAIL — {result.get('detail') or 'not found'}  "
                f"(ok={active} fail={inactive} skip={skipped})"
            )
        if delay and i < total:
            time.sleep(delay)

    stamped = stamp_catalog_rows_from_verify(rows)
    _log(
        f"Done in {_fmt_dur(time.time() - t_loop)}. "
        f"active={active} inactive={inactive} skipped={skipped} "
        f"stamped_rows={stamped}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
