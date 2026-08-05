"""Tests for MoySklad AI playground over golden eval fixture."""

from __future__ import annotations

from plugins.moysklad.ai_playground import (
    build_playground_trace,
    detail_from_facts_payload,
    get_golden_client,
    golden_to_catalog_row,
    list_golden_clients,
    run_playground,
)
from plugins.moysklad.client_card import build_client_detail


def test_list_golden_clients_has_about_20():
    listing = list_golden_clients()
    assert listing["ok"] is True
    assert listing["count"] >= 15
    assert listing["count"] <= 30
    assert len(listing["clients"]) == listing["count"]
    first = listing["clients"][0]
    assert first["id"]
    assert first["name"]
    assert first["order_count"] >= 1


def test_golden_to_detail_maps_orders_and_facts():
    listing = list_golden_clients()
    cid = listing["clients"][0]["id"]
    golden = get_golden_client(cid)
    row = golden_to_catalog_row(golden)
    detail = build_client_detail(row)
    assert detail["client"]["id"] == cid
    assert detail["client"]["name"] == golden["name"]
    assert len(detail["orders"]) >= 1
    assert detail["fact_blocks"]["history_profile"]["lines"]
    assert detail["ai"]["source"] == "heuristic"
    assert detail["ai"]["history_profile"]


def test_run_playground_heuristic_panels():
    listing = list_golden_clients()
    cid = listing["clients"][0]["id"]
    trace = run_playground(client_id=cid, run_llm=False)
    assert trace["ok"] is True
    assert trace["client_id"] == cid
    panels = trace["panels"]
    assert '"client"' in panels["input_text"]
    assert panels["outputs"]["history_profile"]
    assert panels["outputs"]["occasion_intent"]
    assert panels["outputs"]["recommendation"]
    assert "history_profile" in panels["outputs"]["fact_blocks"]
    assert trace["stages"]["llm"] is None
    assert trace["stages"]["active"]["source"] == "heuristic"


def test_run_playground_from_edited_facts_json():
    listing = list_golden_clients()
    cid = listing["clients"][0]["id"]
    base = run_playground(client_id=cid, run_llm=False)
    edited = base["panels"]["input_text"]
    again = run_playground(input_json=edited, run_llm=False)
    assert again["ok"] is True
    assert again["source"] == "facts_edited"
    assert again["panels"]["outputs"]["history_profile"]


def test_detail_from_facts_payload_roundtrip():
    listing = list_golden_clients()
    cid = listing["clients"][0]["id"]
    trace = run_playground(client_id=cid, run_llm=False)
    facts = trace["stages"]["llm_input"]
    detail = detail_from_facts_payload(facts)
    rebuilt = build_playground_trace(detail, run_llm=False, source_label="facts")
    assert rebuilt["panels"]["outputs"]["history_profile"]
