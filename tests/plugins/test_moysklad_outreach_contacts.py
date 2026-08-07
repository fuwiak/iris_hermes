"""Tests for MoySklad outreach contact picker (Рассылки dropdown)."""

from __future__ import annotations

import plugins.moysklad.outreach_contacts as oc


def test_add_and_list_custom_contact(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    added = oc.add_custom_contact(name="Ася", tg_nick="papa2139", tg_chat_id="415321451")
    assert added["id"].startswith("custom:")
    assert added["tg_nick"] == "papa2139"
    assert added["tg_chat_id"] == "415321451"

    listed = oc.list_outreach_contacts()
    assert len(listed) == 1
    assert listed[0]["label"].startswith("Ася")
    assert "@papa2139" in listed[0]["label"]

    got = oc.get_contact(added["id"])
    assert got is not None
    assert got["tg_nick"] == "papa2139"


def test_custom_contact_dedupes_by_nick(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    first = oc.add_custom_contact(name="A", tg_nick="@papa2139")
    second = oc.add_custom_contact(name="B", tg_nick="papa2139", tg_chat_id="1")
    listed = oc.list_outreach_contacts()
    assert len(listed) == 1
    assert listed[0]["id"] == second["id"]
    assert listed[0]["name"] == "B"
    assert first["id"] != second["id"]


def test_add_requires_nick_or_chat_id(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    try:
        oc.add_custom_contact(name="x")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "tg_nick" in str(exc)


def test_delete_custom_contact(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    added = oc.add_custom_contact(tg_nick="someone")
    assert oc.delete_custom_contact(added["id"]) is True
    assert oc.get_contact(added["id"]) is None
    assert oc.delete_custom_contact(added["id"]) is False


def test_list_merges_catalog_clients(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    oc.add_custom_contact(name="Custom", tg_nick="custom_nick")
    catalog = [
        {"id": "cp-1", "name": "Мария", "tg_nick": "maria_flowers"},
        {"id": "cp-2", "name": "NoTG", "tg_nick": ""},
    ]
    listed = oc.list_outreach_contacts(catalog_clients=catalog, q="mar")
    assert len(listed) == 1
    assert listed[0]["id"] == "cp-1"
    assert listed[0]["source"] == "catalog"


def test_seller_settings_seeds_biz_id_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_BUSINESS_CONNECTION_ID", "I2EgEdMmiEvLHAAAmN_NBVfktgQ")
    monkeypatch.delenv("MOYSKLAD_TELEGRAM_BUSINESS_CONNECTION_ID", raising=False)
    from plugins.moysklad import campaigns

    settings = campaigns.get_seller_settings()
    assert settings["telegram_business_connection_id"] == "I2EgEdMmiEvLHAAAmN_NBVfktgQ"
