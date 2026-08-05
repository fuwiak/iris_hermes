#!/usr/bin/env python3
"""Force TELEGRAM_ENABLED=false in /root/deploy.env (Selectel deploy patch)."""

from __future__ import annotations

from pathlib import Path


def main() -> int:
    path = Path("/root/deploy.env")
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    out: list[str] = []
    seen = False
    for line in lines:
        if line.startswith("TELEGRAM_ENABLED="):
            out.append("TELEGRAM_ENABLED=false")
            seen = True
        else:
            out.append(line)
    if not seen:
        out.append("TELEGRAM_ENABLED=false")
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    print("patched TELEGRAM_ENABLED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
