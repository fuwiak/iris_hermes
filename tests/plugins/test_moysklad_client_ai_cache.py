"""Tests for MoySklad client-card DeepSeek AI cache (fingerprint + invalidate)."""

from __future__ import annotations

from pathlib import Path

import pytest

import plugins.moysklad.client_ai_cache as cac
import plugins.moysklad.outreach_cache as oc


@pytest.fixture
def hermes_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("MOYSKLAD_REDIS_URL", raising=False)
    cac.clear_memory_for_tests()
    oc.clear_memory_for_tests()
    return home


def _detail(*, order_count: int = 1, order_sum: float = 100.0) -> dict:
    orders = [
        {
            "id": f"o{i}",
            "date": "2026-03-01 09:55:00",
            "sum": order_sum,
            "payment_status": "paid",
            "channel": "Flow Wow",
        }
        for i in range(order_count)
    ]
    return {
        "client": {"id": "cp-1", "name": "Виктор", "avg_check": order_sum},
        "stats": {
            "order_count": order_count,
            "paid_order_count": order_count,
            "avg_check": order_sum,
            "last_order": orders[-1] if orders else None,
        },
        "orders": orders,
        "conversation": {"message_count": 0},
    }


def test_client_ai_roundtrip_and_fingerprint(hermes_home: Path) -> None:
    detail = _detail(order_count=1)
    fp = cac.facts_fingerprint(detail)
    cac.set_client_ai(
        "cp-1",
        {
            "history_profile": "Виктор: 1 заказ",
            "occasion_intent": "март",
            "recommendation": "написать перед 8 марта",
            "source": "llm",
            "provider": "openrouter",
            "model": "deepseek/deepseek-chat",
        },
        fingerprint=fp,
    )
    hit = cac.get_client_ai("cp-1", fingerprint=fp)
    assert hit is not None
    assert "1 заказ" in hit["history_profile"]
    assert hit["source"] == "llm"

    # Facts changed → stale cache must miss.
    stale = cac.get_client_ai(
        "cp-1", fingerprint=cac.facts_fingerprint(_detail(order_count=3))
    )
    assert stale is None


def test_invalidate_all_client_ai_and_outreach(hermes_home: Path) -> None:
    fp = cac.facts_fingerprint(_detail())
    cac.set_client_ai(
        "cp-1",
        {
            "history_profile": "old",
            "recommendation": "old",
            "source": "llm",
        },
        fingerprint=fp,
    )
    oc.set_outreach_draft(
        "cp-1",
        "telegram",
        {
            "message": "Здравствуйте!",
            "facts": {"history_profile": "old summary"},
            "facts_fingerprint": fp,
        },
    )
    assert cac.get_client_ai("cp-1", fingerprint=fp) is not None
    assert oc.get_outreach_draft("cp-1", "telegram", facts_fingerprint=fp) is not None

    cleared = cac.invalidate_client_ai("")
    assert cleared["ok"] is True
    assert cac.get_client_ai("cp-1", fingerprint=fp) is None

    oc_cleared = oc.invalidate_all_outreach_drafts()
    assert oc_cleared["ok"] is True
    assert oc.get_outreach_draft("cp-1", "telegram") is None


def test_outreach_fingerprint_mismatch_misses(hermes_home: Path) -> None:
    oc.set_outreach_draft(
        "cp-1",
        "telegram",
        {
            "message": "cached",
            "facts_fingerprint": "aaaa",
            "facts": {"history_profile": "stale"},
        },
    )
    assert oc.get_outreach_draft("cp-1", "telegram", facts_fingerprint="bbbb") is None
    assert oc.get_outreach_draft("cp-1", "telegram", facts_fingerprint="aaaa") is not None
