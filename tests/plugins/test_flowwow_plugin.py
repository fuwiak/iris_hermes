"""Flowwow plugin: registration, availability gating, health handler wiring."""

from __future__ import annotations

import plugins.flowwow as plugin
from plugins.flowwow.tools import check_flowwow_available, handle_flowwow_health


def test_register_wires_three_tools():
    calls: list[dict] = []

    class _Ctx:
        def register_tool(self, **kw):
            calls.append(kw)

    plugin.register(_Ctx())
    names = {c["name"] for c in calls}
    assert names == {"flowwow_health", "flowwow_orders", "flowwow_clients"}
    assert all(c["toolset"] == "flowwow" for c in calls)
    assert all(c["check_fn"] is check_flowwow_available for c in calls)


def test_check_available_reflects_token_env(monkeypatch):
    monkeypatch.delenv("FLOWWOW_API_TOKEN", raising=False)
    assert check_flowwow_available() is False
    monkeypatch.setenv("FLOWWOW_API_TOKEN", "tok")
    assert check_flowwow_available() is True


def test_health_handler_reports_missing_token(monkeypatch):
    monkeypatch.delenv("FLOWWOW_API_TOKEN", raising=False)
    out = handle_flowwow_health({})
    assert "FLOWWOW_API_TOKEN" in out
