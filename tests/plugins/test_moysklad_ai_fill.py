"""Tests for MoySklad AI empty-field fill + green-marker stamps."""

from __future__ import annotations

from plugins.moysklad.ai_fill import (
    apply_ai_fill_to_public,
    empty_fillable_keys,
    fill_empty_for_rows,
    heuristic_fill_row,
    is_empty_cell,
)
from plugins.moysklad.classify import _public_client


def test_is_empty_and_fillable_keys():
    row = {
        "_moysklad_id": "c1",
        "Наименование": "Мария Цветы",
        "Статус": "",
        "Пол": "",
        "_moysklad_tags": [],
        "order_count": 3,
        "avg_check": 12000,
    }
    assert is_empty_cell("")
    assert is_empty_cell("—")
    assert "state" in empty_fillable_keys(row)
    assert "groups" in empty_fillable_keys(row)
    assert "sex" in empty_fillable_keys(row)


def test_heuristic_fill_groups_sex_state(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    row = {
        "_moysklad_id": "c2",
        "Наименование": "Мария Букет",
        "order_count": 4,
        "avg_check": 15000,
        "_moysklad_tags": [],
        "_orders_context": [
            {"Канал продаж": "WhatsApp", "moment": "2026-03-01 10:00:00"},
        ],
    }
    fills = heuristic_fill_row(row)
    assert fills.get("sex") == "Женский"
    assert fills.get("state") in {"активный", "новый", "спящий"}
    assert fills.get("groups")
    assert "постоянный клиент" in fills["groups"] or "премиум" in fills["groups"] or fills["groups"]


def test_fill_empty_persists_ai_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    rows = [
        {
            "_moysklad_id": "c3",
            "Наименование": "Анна Роза",
            "order_count": 1,
            "avg_check": 3000,
            "_moysklad_tags": [],
            "_orders_context": [],
        }
    ]
    out = fill_empty_for_rows(rows, use_llm=False, limit=10)
    assert out["updated"] == 1
    assert out["results"][0]["ai_fields"]
    public = apply_ai_fill_to_public({"id": "c3", "name": "Анна Роза", "state": "", "groups": "", "tags": [], "sex": ""})
    assert public.get("ai_fields")
    assert public.get("state") or public.get("sex") or public.get("groups")


def test_public_client_exposes_ai_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    row = {
        "_moysklad_id": "c4",
        "Наименование": "Ольга Пион",
        "order_count": 2,
        "avg_check": 8000,
        "_moysklad_tags": [],
        "_audience": {"direct": True, "marketplace": False},
        "_orders_context": [{"Канал продаж": "Telegram"}],
    }
    fill_empty_for_rows([row], use_llm=False, limit=5)
    public = _public_client(row)
    assert "ai_fields" in public
    # At least one empty slot filled and stamped
    assert public["ai_fields"]
    for key in public["ai_fields"]:
        assert not is_empty_cell(public.get(key) if key != "groups" else public.get("groups"))
