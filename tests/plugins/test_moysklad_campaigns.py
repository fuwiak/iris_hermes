"""Unit tests for MoySklad campaign draft store."""

from __future__ import annotations

import plugins.moysklad.campaigns as campaigns


def test_create_list_delete_campaign(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    item = campaigns.create_draft(
        title="Test blast",
        channel="telegram",
        mode="manual",
        offer="Hello",
        sales_filter="direct",
        audience_count=3,
        audience_preview=[{"name": "A"}],
    )
    assert item["id"]
    assert item["status"] == "draft"
    listed = campaigns.list_campaigns()
    assert len(listed) == 1
    assert listed[0]["title"] == "Test blast"
    assert campaigns.delete_campaign(item["id"]) is True
    assert campaigns.list_campaigns() == []
    assert campaigns.delete_campaign(item["id"]) is False
