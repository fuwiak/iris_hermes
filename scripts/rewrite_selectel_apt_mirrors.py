#!/usr/bin/env python3
"""Rewrite dead Selectel HTTP Ubuntu apt mirrors.

Selectel VDS images pin ``http://mirror.selectel.ru/ubuntu``. From ru-7 that
host is reached over IPv6 and fails with Errno 101 (Network is unreachable);
apt then reports the repo "no longer has a Release file" and
``remote_deploy.sh`` dies before docker compose.

Ubuntu's archive over IPv4 still works. Leave ``mirror.selectel.ru/3rd-party/``
alone — that HTTPS repo is a different path and still answers.
"""

from __future__ import annotations

import argparse
from pathlib import Path

SELECTEL_UBUNTU = "mirror.selectel.ru/ubuntu"
REPLACEMENT = "http://archive.ubuntu.com/ubuntu"
FORCE_IPV4 = 'Acquire::ForceIPv4 "true";\n'


def rewrite_text(text: str) -> tuple[str, int]:
    """Replace http(s)://mirror.selectel.ru/ubuntu → archive.ubuntu.com.

    Returns (new_text, replacement_count).
    """
    count = 0
    out = text
    for scheme in ("https://", "http://"):
        needle = f"{scheme}{SELECTEL_UBUNTU}"
        n = out.count(needle)
        if n:
            out = out.replace(needle, REPLACEMENT)
            count += n
    return out, count


def iter_source_files(root: Path) -> list[Path]:
    files: list[Path] = []
    sources = root / "sources.list"
    if sources.is_file():
        files.append(sources)
    extra = root / "sources.list.d"
    if extra.is_dir():
        files.extend(sorted(p for p in extra.iterdir() if p.is_file() and not p.is_symlink()))
    return files


def rewrite_tree(root: Path) -> list[Path]:
    changed: list[Path] = []
    for path in iter_source_files(root):
        original = path.read_text(encoding="utf-8", errors="replace")
        new, n = rewrite_text(original)
        if n:
            path.write_text(new, encoding="utf-8")
            changed.append(path)
    return changed


def write_force_ipv4(root: Path) -> Path:
    conf_d = root / "apt.conf.d"
    conf_d.mkdir(parents=True, exist_ok=True)
    path = conf_d / "99force-ipv4"
    path.write_text(FORCE_IPV4, encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default="/etc/apt",
        help="apt config root (default /etc/apt; tests pass a temp dir)",
    )
    parser.add_argument(
        "--no-ipv4",
        action="store_true",
        help="do not write Acquire::ForceIPv4",
    )
    args = parser.parse_args(argv)
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    if not args.no_ipv4:
        ipv4 = write_force_ipv4(root)
        print(f"wrote {ipv4}")
    changed = rewrite_tree(root)
    if changed:
        for path in changed:
            print(f"rewrote {path} → {REPLACEMENT}")
    else:
        print("no Selectel ubuntu apt URIs found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
