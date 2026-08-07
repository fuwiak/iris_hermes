"""Tests for Telegram Desktop export → MoySklad conversation mapping."""

from __future__ import annotations

import json
from pathlib import Path

from plugins.moysklad.conversations import get_thread
from plugins.moysklad.telegram_export import (
    apply_export_overlay_to_public,
    clear_import_memory_for_tests,
    import_export_into_catalog,
    load_overlay,
)


def _mini_export(tmp: Path) -> Path:
    payload = {
        "personal_information": {
            "user_id": 111,
            "first_name": "Studio",
            "username": "@studio",
        },
        "contacts": {
            "list": [
                {
                    "first_name": "Мария",
                    "last_name": "Букет",
                    "phone_number": "0079005551122",
                }
            ]
        },
        "chats": {
            "list": [
                {
                    "name": "Мария Букет",
                    "type": "personal_chat",
                    "id": 999001,
                    "messages": [
                        {
                            "id": 1,
                            "type": "message",
                            "date": "2026-03-01T10:00:00",
                            "from": "Мария Букет",
                            "from_id": "user999001",
                            "text": "Здравствуйте, хочу букет",
                            "text_entities": [{"type": "plain", "text": "Здравствуйте, хочу букет"}],
                        },
                        {
                            "id": 2,
                            "type": "message",
                            "date": "2026-03-01T10:05:00",
                            "from": "Studio",
                            "from_id": "user111",
                            "text": "Добрый день!",
                            "text_entities": [{"type": "plain", "text": "Добрый день!"}],
                        },
                        {
                            "id": 3,
                            "type": "message",
                            "date": "2026-03-01T10:06:00",
                            "from": "Мария Букет",
                            "from_id": "user999001",
                            "text": "Пишите мне @maria_flowers_msk",
                            "text_entities": [
                                {"type": "plain", "text": "Пишите мне "},
                                {"type": "mention", "text": "@maria_flowers_msk"},
                            ],
                        },
                    ],
                }
            ]
        },
    }
    path = tmp / "telegram_export.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_import_export_maps_phone_and_nick(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    clear_import_memory_for_tests()
    export_path = _mini_export(tmp_path)

    rows = [
        {
            "_moysklad_id": "cp-maria",
            "Наименование": "Мария Букет",
            "Телефон": "+7 (900) 555-11-22",
            "ТГ ник": "",
        }
    ]
    result = import_export_into_catalog(rows, export_path=export_path, force=True)
    assert result["ok"] is True
    assert result["matched"] == 1
    assert result["imported_messages"] >= 2
    assert result["nick_filled"] == 1
    assert rows[0]["ТГ ник"].lower().endswith("maria_flowers_msk")

    thread = get_thread(client_id="cp-maria", phone="9005551122")
    assert thread["message_count"] >= 2
    assert any("букет" in str(m.get("text") or "").lower() for m in thread["messages"])

    overlay = load_overlay()
    entry = overlay["by_client_id"]["cp-maria"]
    assert entry["tg_chat_id"] == "999001"
    assert "maria_flowers_msk" in str(entry.get("tg_nick") or "").lower()

    public = apply_export_overlay_to_public({"id": "cp-maria", "tg_nick": ""})
    assert "maria_flowers_msk" in str(public.get("tg_nick") or "").lower()
    assert public.get("tg_chat_id") == "999001"


def test_import_skipped_without_export(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("MOYSKLAD_TELEGRAM_EXPORT", raising=False)
    clear_import_memory_for_tests()
    # No export file in HERMES_HOME / cwd data
    result = import_export_into_catalog(
        [{"_moysklad_id": "x", "Наименование": "A", "Телефон": "9001112233"}],
        export_path=tmp_path / "missing.json",
        force=True,
    )
    assert result["ok"] is False
    assert result["error"] == "export_not_found"
