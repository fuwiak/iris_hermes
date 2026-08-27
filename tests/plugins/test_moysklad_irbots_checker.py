"""Unit tests for IRbots phone checker + credit cache."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("IRBOTS_API_KEY", "test-key")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("MOYSKLAD_REDIS_URL", raising=False)
    monkeypatch.setenv("MOYSKLAD_API_TOKEN", "test-token")
    from plugins.moysklad import irbots_checker, tg_verify

    irbots_checker._MEMORY = None
    irbots_checker._MEMORY_FP = None
    tg_verify._MEMORY = None
    tg_verify._MEMORY_FP = None
    yield
    irbots_checker._MEMORY = None
    irbots_checker._MEMORY_FP = None
    tg_verify._MEMORY = None
    tg_verify._MEMORY_FP = None


def test_map_status_session_active_true_string_inactive():
    from plugins.moysklad.irbots_checker import map_status

    assert map_status("session") == (True, "session")
    assert map_status("used") == (True, "used")
    assert map_status("true") == (False, "true")  # unregistered
    assert map_status(True) == (False, "true")  # JSON true = FAQ «True»
    assert map_status("banned") == (False, "banned")
    assert map_status("ban") == (False, "ban")
    assert map_status("invalid") == (False, "invalid")
    assert map_status(False) == (False, "false")


def test_resolve_phones_uses_cache_and_skips_second_api_call(monkeypatch):
    from plugins.moysklad import irbots_checker

    calls: list[list[str]] = []

    def _fake_remote(numbers, **_kw):
        batch = list(numbers)
        calls.append(batch)
        return {
            "ok": True,
            "data": {p: "session" if p.endswith("8058") else "true" for p in batch},
            "errors": 0,
            "status": "ok",
        }

    monkeypatch.setattr(irbots_checker, "check_numbers_remote", _fake_remote)

    first = irbots_checker.resolve_phones(["+79775758058", "+79822352188"])
    assert len(calls) == 1
    assert first["+79775758058"]["active"] is True
    assert first["+79775758058"]["cached"] is False
    assert first["+79822352188"]["active"] is False

    second = irbots_checker.resolve_phones(["+79775758058", "+79822352188"])
    assert len(calls) == 1  # no second HTTP — credit cache
    assert second["+79775758058"]["cached"] is True
    assert second["+79822352188"]["cached"] is True


def test_verify_rows_writes_overlay_active_and_inactive(monkeypatch):
    from plugins.moysklad import irbots_checker, tg_verify

    monkeypatch.setattr(
        irbots_checker,
        "resolve_phones",
        lambda phones, **kw: {
            "+79775758058": {
                "status": "session",
                "active": True,
                "label": "активный",
                "checked_at": 1.0,
                "cached": False,
            },
            "+79822352188": {
                "status": "true",
                "active": False,
                "label": "неактивный",
                "checked_at": 1.0,
                "cached": False,
            },
        },
    )

    rows = [
        {"_moysklad_id": "c1", "Наименование": "A", "Телефон": "+7 977 575-80-58"},
        {"_moysklad_id": "c2", "Наименование": "B", "Телефон": "+7 982 235-21-88"},
        {"_moysklad_id": "c3", "Наименование": "C"},
    ]
    stats = irbots_checker.verify_rows_via_irbots(rows, only_unchecked=False)
    assert stats["active"] == 1
    assert stats["inactive"] == 1
    assert tg_verify.overlay_for_client("c1")["active"] is True
    assert tg_verify.overlay_for_client("c2")["active"] is False
    assert not tg_verify.overlay_for_client("c3")


def test_force_complete_and_report_binary_only(tmp_path, monkeypatch):
    from plugins.moysklad import irbots_checker, tg_verify

    rows = [
        {
            "_moysklad_id": "c1",
            "Наименование": "Активный",
            "Телефон": "+79153588839",
        },
        {
            "_moysklad_id": "c2",
            "Наименование": "Без телефона",
            "Телефон": "",
        },
    ]
    tg_verify.save_verify_results_bulk(
        {
            "c1": {
                "active": True,
                "via": "irbots",
                "detail": "активный (есть сессия TG)",
            }
        }
    )
    out = irbots_checker.force_complete_unchecked_rows(rows)
    assert out["forced"] == 1
    assert tg_verify.overlay_for_client("c2")["active"] is False
    text = Path(out["report"]).read_text(encoding="utf-8")
    assert "НЕ ПРОВЕРЕН" not in text
    assert text.count("status=АКТИВНЫЙ") == 1
    assert text.count("status=НЕАКТИВНЫЙ") == 1
    assert "unchecked=0" in text


def test_write_full_report_contains_verdict(tmp_path, monkeypatch):
    from plugins.moysklad import irbots_checker, tg_verify

    tg_verify.save_verify_results_bulk(
        {
            "c1": {
                "active": True,
                "via": "irbots",
                "detail": "активный (есть сессия TG)",
            }
        }
    )
    rows = [
        {
            "_moysklad_id": "c1",
            "Наименование": "Виктор",
            "Телефон": "+79153588839",
            "Группы": "тест",
        },
        {
            "_moysklad_id": "c2",
            "Наименование": "Без оверлея",
            "Телефон": "",
        },
    ]
    path = irbots_checker.write_full_report(rows, path=tmp_path / "report.txt")
    text = path.read_text(encoding="utf-8")
    assert "АКТИВНЫЙ" in text
    assert "Виктор" in text
    assert "+79153588839" in text
    assert "НЕ ПРОВЕРЕН" not in text
    assert "НЕАКТИВНЫЙ" in text
