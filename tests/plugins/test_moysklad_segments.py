"""Именованные списки клиентов — сохранённые фильтры Рассылок."""

from __future__ import annotations

from plugins.moysklad.segments import (
    clear_memory_for_tests,
    delete_segment,
    get_segment,
    list_segments,
    normalize_filters,
    save_segment,
)


def test_normalize_filters_keeps_only_known_keys():
    out = normalize_filters({
        "sales_filter": "direct",
        "vip_only": "true",
        "days_before_event": "5",
        "unknown_field": "x",
        "q": "",
    })
    assert out == {"sales_filter": "direct", "vip_only": True, "days_before_event": 5}


def test_save_and_list_segments(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    clear_memory_for_tests()

    seg = save_segment(
        name="Не состоялся · Прямые",
        filters={"sales_filter": "direct", "stage": "failed"},
        matched_total=42,
    )
    assert seg["name"] == "Не состоялся · Прямые"
    assert seg["filters"] == {"sales_filter": "direct", "stage": "failed"}
    assert seg["matched_total"] == 42
    assert seg["id"].startswith("seg-")

    listed = list_segments()
    assert len(listed) == 1
    assert listed[0]["id"] == seg["id"]

    fetched = get_segment(seg["id"])
    assert fetched is not None
    assert fetched["name"] == seg["name"]


def test_update_existing_segment_keeps_id_and_created_at(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    clear_memory_for_tests()

    first = save_segment(name="VIP Telegram", filters={"vip_only": True})
    second = save_segment(
        segment_id=first["id"],
        name="VIP Telegram (обновлено)",
        filters={"vip_only": True, "require_telegram": True},
    )
    assert second["id"] == first["id"]
    assert second["created_at"] == first["created_at"]
    assert second["name"] == "VIP Telegram (обновлено)"
    assert len(list_segments()) == 1


def test_save_without_name_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    clear_memory_for_tests()
    try:
        save_segment(name="  ", filters={})
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_delete_segment(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    clear_memory_for_tests()
    seg = save_segment(name="Temp", filters={"stage": "no_orders"})
    assert delete_segment(seg["id"]) is True
    assert get_segment(seg["id"]) is None
    assert delete_segment(seg["id"]) is False


def test_segments_persist_to_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    clear_memory_for_tests()
    save_segment(name="Персист", filters={"sales_filter": "marketplace"})
    path = tmp_path / "moysklad" / "segments.json"
    assert path.is_file()

    clear_memory_for_tests()
    listed = list_segments()
    assert len(listed) == 1
    assert listed[0]["name"] == "Персист"
