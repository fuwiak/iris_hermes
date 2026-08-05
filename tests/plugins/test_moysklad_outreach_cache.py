"""Tests for durable MoySklad outreach draft cache (Redis → file → memory)."""

from __future__ import annotations

from pathlib import Path

import pytest

import plugins.moysklad.outreach_cache as oc


@pytest.fixture
def hermes_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("MOYSKLAD_REDIS_URL", raising=False)
    oc.clear_memory_for_tests()
    return home


def test_outreach_draft_file_roundtrip(hermes_home: Path) -> None:
    assert oc.get_outreach_draft("c1", "telegram") is None
    oc.set_outreach_draft(
        "c1",
        "telegram",
        {
            "message": "Здравствуйте, Мария!",
            "grounding_notes": "пионы",
            "source": "llm",
            "status": "AI сгенерировал креативный текст — можно править вручную.",
            "client_name": "Мария",
            "facts": {"name": "Мария"},
        },
    )
    hit = oc.get_outreach_draft("c1", "telegram")
    assert hit is not None
    assert hit["message"].startswith("Здравствуйте")
    assert hit["client_name"] == "Мария"
    assert oc.cache_backend_name() == "file"
    assert (hermes_home / "moysklad" / "outreach_cache").is_dir()


def test_outreach_draft_channel_isolation(hermes_home: Path) -> None:
    oc.set_outreach_draft("c1", "telegram", {"message": "tg text"})
    oc.set_outreach_draft("c1", "whatsapp", {"message": "wa text"})
    assert oc.get_outreach_draft("c1", "telegram")["message"] == "tg text"
    assert oc.get_outreach_draft("c1", "whatsapp")["message"] == "wa text"


def test_outreach_draft_invalidate(hermes_home: Path) -> None:
    oc.set_outreach_draft("c1", "telegram", {"message": "x"})
    oc.invalidate_outreach_draft("c1", "telegram")
    assert oc.get_outreach_draft("c1", "telegram") is None


def test_outreach_draft_requires_message(hermes_home: Path) -> None:
    with pytest.raises(ValueError):
        oc.set_outreach_draft("c1", "telegram", {"message": "  "})
