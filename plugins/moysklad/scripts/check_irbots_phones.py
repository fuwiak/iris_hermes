#!/usr/bin/env python3
"""Bulk-check all MoySklad clients via IRbots and write a text report.

Usage (from repo root, with HERMES_HOME / IRBOTS_API_KEY loaded)::

    python -m plugins.moysklad.scripts.check_irbots_phones
    python -m plugins.moysklad.scripts.check_irbots_phones --force
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _log(msg: str) -> None:
    print(msg, flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-query IRbots even when phone/client already cached",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=None,
        help="Optional path to catalog JSON envelope (else newest cache file)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Output text path (default $HERMES_HOME/moysklad/irbots_clients_status.txt)",
    )
    args = parser.parse_args(argv)

    # Load ~/.hermes/.env without shell-sourcing (quotes/spaces break source).
    try:
        from dotenv import load_dotenv

        home_env = Path.home() / ".hermes" / ".env"
        if home_env.is_file():
            load_dotenv(home_env, override=False)
        repo_env = Path(__file__).resolve().parents[3] / ".env"
        if repo_env.is_file():
            load_dotenv(repo_env, override=False)
    except Exception:
        pass

    if not (os.environ.get("IRBOTS_API_KEY") or "").strip():
        _log("ERROR: IRBOTS_API_KEY missing")
        return 2

    from hermes_constants import get_hermes_home
    from plugins.moysklad.irbots_checker import (
        verify_rows_via_irbots,
        write_full_report,
    )
    from plugins.moysklad.tg_verify import persist_verify_into_catalog

    catalog_path = args.catalog
    if catalog_path is None:
        cache_dir = get_hermes_home() / "moysklad" / "cache"
        candidates = sorted(
            cache_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for path in candidates:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(raw, dict) and isinstance(raw.get("catalog"), dict):
                catalog_path = path
                break
        if catalog_path is None:
            _log("ERROR: no catalog cache with rows found")
            return 3

    envelope = json.loads(catalog_path.read_text(encoding="utf-8"))
    rows = list((envelope.get("catalog") or {}).get("rows") or [])
    _log(f"Catalog {catalog_path.name}: {len(rows)} rows")

    stats = verify_rows_via_irbots(
        rows,
        only_unchecked=not args.force,
        force=args.force,
    )
    _log(f"IRbots stats: {stats}")

    persisted = persist_verify_into_catalog(rows)
    _log(f"Catalog persisted: {persisted}")

    report = write_full_report(rows, path=args.report)
    _log(f"Report: {report}")
    # Re-apply report as UI source of truth (strips AI «новый» from Статус).
    from plugins.moysklad.irbots_checker import apply_status_report_file

    applied = apply_status_report_file(report, rows=rows)
    _log(f"Status file applied: {applied}")
    # Workspace-accessible copy (gitignored data/).
    try:
        repo_copy = Path(__file__).resolve().parents[3] / "data" / "irbots_clients_status.txt"
        repo_copy.parent.mkdir(parents=True, exist_ok=True)
        repo_copy.write_text(report.read_text(encoding="utf-8"), encoding="utf-8")
        _log(f"Repo copy: {repo_copy}")
    except Exception as exc:
        _log(f"Repo copy skipped: {exc}")
    return 0 if not stats.get("error") else 1


if __name__ == "__main__":
    sys.exit(main())
