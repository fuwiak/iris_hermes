#!/usr/bin/env python3
"""Upsert MoySklad secrets into HERMES_HOME/.env from Railway CLI.

Usage:
  scripts/sync_moysklad_env_from_railway.py
  scripts/sync_moysklad_env_from_railway.py --env-file ~/.hermes/.env

Never prints secret values. Requires `railway` CLI linked to the project.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

KEYS = (
    "MOYSKLAD_API_TOKEN",
    "MOYSKLAD_API_URL",
    "MOYSKLAD_AUTO_SYNC",
    "MOYSKLAD_ENABLED",
    "MOYSKLAD_SYNC_LIMIT",
    "MOYSKLAD_SYNC_ORDERS_LIMIT",
)


def _fetch_from_railway() -> dict[str, str]:
    if not shutil.which("railway"):
        raise SystemExit(
            "railway CLI not found. Install it, then `railway link` this repo."
        )
    proc = subprocess.run(
        ["railway", "variable", "list", "--json"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise SystemExit(f"railway variable list failed: {err}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"railway returned non-JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit("unexpected railway JSON shape (expected object)")
    out: dict[str, str] = {}
    for key in KEYS:
        raw = data.get(key)
        if raw is None:
            continue
        value = str(raw).strip()
        if value:
            out[key] = value
    if "MOYSKLAD_API_TOKEN" not in out:
        raise SystemExit(
            "Railway has no MOYSKLAD_API_TOKEN. Set it with:\n"
            "  railway variable set MOYSKLAD_API_TOKEN=<token>"
        )
    return out


def _fetch_from_process_env() -> dict[str, str]:
    out: dict[str, str] = {}
    for key in KEYS:
        value = (os.environ.get(key) or "").strip()
        if value:
            out[key] = value
    return out


def upsert_env_file(path: Path, mapping: dict[str, str]) -> tuple[int, int]:
    """Rewrite KEY=value lines in place. Returns (updated, added)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = text.splitlines()
    found = {key: False for key in mapping}
    out: list[str] = []
    for line in lines:
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if match and match.group(1) in mapping:
            key = match.group(1)
            out.append(f"{key}={mapping[key]}")
            found[key] = True
        else:
            out.append(line)
    missing = [key for key, ok in found.items() if not ok]
    added = 0
    if missing:
        if out and out[-1].strip():
            out.append("")
        out.append("# MoySklad Remap 1.2 (synced from Railway / process env)")
        for key in missing:
            out.append(f"{key}={mapping[key]}")
            added += 1
    updated = sum(1 for ok in found.values() if ok)
    new_text = "\n".join(out) + ("\n" if out else "")
    if new_text != text:
        path.write_text(new_text, encoding="utf-8")
    return updated, added


def default_env_path() -> Path:
    home = os.environ.get("HERMES_HOME")
    if home:
        return Path(home) / ".env"
    return Path.home() / ".hermes" / ".env"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Target .env path (default: $HERMES_HOME/.env or ~/.hermes/.env)",
    )
    parser.add_argument(
        "--prefer-process-env",
        action="store_true",
        help="Prefer already-exported env vars (Railway container) over CLI",
    )
    args = parser.parse_args(argv)

    mapping = _fetch_from_process_env() if args.prefer_process_env else {}
    if "MOYSKLAD_API_TOKEN" not in mapping:
        mapping = _fetch_from_railway()

    target = args.env_file or default_env_path()
    updated, added = upsert_env_file(target, mapping)
    token_len = len(mapping.get("MOYSKLAD_API_TOKEN", ""))
    print(
        f"✓ {target}: updated={updated} added={added} "
        f"MOYSKLAD_API_TOKEN len={token_len}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
