#!/usr/bin/env python3
"""Upsert deploy secrets into HERMES_HOME/.env from Railway CLI or process env.

Usage:
  scripts/sync_moysklad_env_from_railway.py
  scripts/sync_moysklad_env_from_railway.py --env-file ~/.hermes/.env
  scripts/sync_moysklad_env_from_railway.py --prefer-process-env

Never prints secret values.

Why this exists for OpenRouter: Hermes ``load_hermes_dotenv`` loads
``$HERMES_HOME/.env`` with ``override=True``, so a stale volume key wins over
compose ``env_file`` after key rotation — chat then keeps returning OpenRouter
HTTP 403 ``Access denied by security policy``. Boot sync copies process-env
keys into the volume file so deploy.env updates take effect.
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

MOYSKLAD_KEYS = (
    "MOYSKLAD_API_TOKEN",
    "MOYSKLAD_API_URL",
    "MOYSKLAD_AUTO_SYNC",
    "MOYSKLAD_ENABLED",
    "MOYSKLAD_SYNC_LIMIT",
    "MOYSKLAD_SYNC_ORDERS_LIMIT",
    "MOYSKLAD_TELEGRAM_BOT_TOKEN",
    "MOYSKLAD_TELEGRAM_BOT_USERNAME",
    "MOYSKLAD_TELEGRAM_BUSINESS_CONNECTION_ID",
)

# LLM provider keys that must follow compose/deploy.env after rotation.
# OPENROUTER_BASE_URL: Railway egress proxy (Selectel RU → non-RU IP).
LLM_KEYS = (
    "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL",
    "DEEPSEEK_API_KEY",
)

# Back-compat alias for importers / older call sites.
KEYS = MOYSKLAD_KEYS


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
    for key in MOYSKLAD_KEYS:
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


def _fetch_keys_from_process_env(keys: tuple[str, ...]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in keys:
        value = (os.environ.get(key) or "").strip()
        if value:
            out[key] = value
    return out


def _fetch_from_process_env() -> dict[str, str]:
    """MoySklad + LLM keys present in the process environment."""
    out = _fetch_keys_from_process_env(MOYSKLAD_KEYS)
    out.update(_fetch_keys_from_process_env(LLM_KEYS))
    return out


def upsert_env_file(path: Path, mapping: dict[str, str]) -> tuple[int, int]:
    """Rewrite KEY=value lines in place. Returns (updated, added)."""
    if not mapping:
        return 0, 0
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
        out.append("# Synced from process env / Railway (deploy secrets)")
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
        help="Prefer already-exported env vars (container / compose) over CLI",
    )
    args = parser.parse_args(argv)

    if args.prefer_process_env:
        mapping = _fetch_from_process_env()
        # LLM-only rotation is valid — do not fall back to Railway CLI.
        if not mapping:
            print("✓ nothing to sync from process env")
            return 0
    else:
        mapping = _fetch_from_railway()

    target = args.env_file or default_env_path()
    updated, added = upsert_env_file(target, mapping)
    # Lengths only — never print secret values.
    bits = [f"updated={updated}", f"added={added}"]
    for key in ("MOYSKLAD_API_TOKEN", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY"):
        if key in mapping:
            bits.append(f"{key}_len={len(mapping[key])}")
    print(f"✓ {target}: " + " ".join(bits))
    return 0


if __name__ == "__main__":
    sys.exit(main())
