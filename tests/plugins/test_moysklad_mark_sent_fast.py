"""mark-sent / mass-send must resolve TG targets without build_client_detail.

That call pulls MoySklad order compositions and routinely burns 15–60s —
past the desktop default fetch timeout — so «Отправляю…» never finished.
"""

from __future__ import annotations

from plugins.moysklad.dashboard.plugin_api import (
    _outreach_target_from_row,
    _resolve_mass_send_target,
)


def test_outreach_target_from_row_reads_catalog_fields_only():
    row = {
        "_moysklad_id": "cp-hans",
        "Наименование": "Hans",
        "Телефон": "+79991234567",
        "ТГ ник": "@pawels2137",
        "ТГ chat id": "12345",
        "TG conversation": "https://t.me/pawels2137",
    }
    target = _outreach_target_from_row(row)
    assert target["client_name"] == "Hans"
    assert target["phone"] == "+79991234567"
    assert target["tg_nick"] == "@pawels2137"
    assert target["tg_chat_id"] == "12345"
    assert target["telegram_url"] == "https://t.me/pawels2137"
    assert "wa.me" in target["whatsapp_url"]


def test_mass_send_target_skips_build_client_detail(monkeypatch):
    """Regression: per-recipient build_client_detail hung mass jobs."""
    calls: list[str] = []

    def boom(*_a, **_k):
        calls.append("build")
        raise AssertionError("build_client_detail must not run on send path")

    monkeypatch.setattr(
        "plugins.moysklad.dashboard.plugin_api.build_client_detail", boom
    )
    monkeypatch.setattr(
        "plugins.moysklad.dashboard.plugin_api.find_row_in_catalog",
        lambda _c, _id: {
            "_moysklad_id": "cp-1",
            "Наименование": "Hans",
            "ТГ ник": "@pawels2137",
        },
    )
    target = _resolve_mass_send_target({"rows": []}, "cp-1")
    assert target is not None
    assert target["tg_nick"] == "@pawels2137"
    assert target["client_name"] == "Hans"
    assert calls == []
