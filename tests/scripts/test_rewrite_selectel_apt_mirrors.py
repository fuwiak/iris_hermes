"""Selectel HTTP ubuntu mirror must be rewritten before apt-get update."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "rewrite_selectel_apt_mirrors.py"
)


def _load_mod():
    spec = importlib.util.spec_from_file_location("rewrite_selectel_apt_mirrors", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_rewrites_http_and_https_ubuntu_uris() -> None:
    mod = _load_mod()
    text = (
        "deb http://mirror.selectel.ru/ubuntu noble main\n"
        "URIs: https://mirror.selectel.ru/ubuntu\n"
        "deb https://mirror.selectel.ru/3rd-party/cloud-init-deb/noble noble main\n"
    )
    new, n = mod.rewrite_text(text)
    assert n == 2
    assert "mirror.selectel.ru/ubuntu" not in new
    assert "http://archive.ubuntu.com/ubuntu" in new
    assert "mirror.selectel.ru/3rd-party/" in new


def test_rewrite_tree_and_force_ipv4(tmp_path: Path) -> None:
    mod = _load_mod()
    (tmp_path / "sources.list.d").mkdir()
    (tmp_path / "sources.list").write_text(
        "deb http://mirror.selectel.ru/ubuntu noble main restricted\n",
        encoding="utf-8",
    )
    deb822 = tmp_path / "sources.list.d" / "ubuntu.sources"
    deb822.write_text(
        "Types: deb\nURIs: http://mirror.selectel.ru/ubuntu\nSuites: noble\n",
        encoding="utf-8",
    )
    third = tmp_path / "sources.list.d" / "selectel-cloud-init.list"
    third.write_text(
        "deb https://mirror.selectel.ru/3rd-party/cloud-init-deb/noble noble main\n",
        encoding="utf-8",
    )

    assert mod.main(["--root", str(tmp_path)]) == 0

    sources = (tmp_path / "sources.list").read_text(encoding="utf-8")
    ubuntu = deb822.read_text(encoding="utf-8")
    assert "archive.ubuntu.com/ubuntu" in sources
    assert "archive.ubuntu.com/ubuntu" in ubuntu
    assert "mirror.selectel.ru/ubuntu" not in sources
    assert "mirror.selectel.ru/3rd-party/" in third.read_text(encoding="utf-8")
    assert 'Acquire::ForceIPv4 "true"' in (tmp_path / "apt.conf.d" / "99force-ipv4").read_text(
        encoding="utf-8"
    )
