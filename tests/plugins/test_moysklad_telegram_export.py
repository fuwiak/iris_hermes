"""Tests for Telegram Desktop export → MoySklad conversation mapping."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from plugins.moysklad.conversations import clear_memory_for_tests, get_thread
from plugins.moysklad.telegram_export import (
    apply_export_overlay_to_public,
    clear_import_memory_for_tests,
    ensure_export_imported,
    import_export_into_catalog,
    load_overlay,
    stamp_catalog_rows_from_overlay,
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
    clear_memory_for_tests()
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
    conv = str(rows[0].get("TG conversation") or "").lower()
    assert "maria_flowers_msk" in conv or "пишите" in conv or "букет" in conv
    assert result.get("cache_backend") in {"file", "redis+file"}

    # Overlay + conversations land on durable file cache under HERMES_HOME.
    overlay_file = tmp_path / "moysklad" / "telegram_export_overlay.json"
    conv_file = tmp_path / "moysklad" / "conversations.json"
    assert overlay_file.is_file()
    assert conv_file.is_file()

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


def test_stamp_from_cached_overlay_without_export(tmp_path, monkeypatch):
    """After import, overlay alone fills empty ТГ fields (Railway: no local export)."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("MOYSKLAD_TELEGRAM_EXPORT", raising=False)
    clear_import_memory_for_tests()
    clear_memory_for_tests()
    export_path = _mini_export(tmp_path)
    import_export_into_catalog(
        [
            {
                "_moysklad_id": "cp-maria",
                "Наименование": "Мария Букет",
                "Телефон": "+7 (900) 555-11-22",
                "ТГ ник": "",
            }
        ],
        export_path=export_path,
        force=True,
    )
    # Simulate restart: drop memory, remove export, stamp from file overlay.
    clear_import_memory_for_tests()
    clear_memory_for_tests()
    export_path.unlink()
    rows = [
        {
            "_moysklad_id": "cp-maria",
            "Наименование": "Мария Букет",
            "Телефон": "+7 (900) 555-11-22",
            "ТГ ник": "",
            "TG conversation": "",
        }
    ]
    stamped = stamp_catalog_rows_from_overlay(rows)
    assert stamped == 1
    assert "maria_flowers_msk" in str(rows[0].get("ТГ ник") or "").lower()
    assert str(rows[0].get("tg_chat_id") or "") == "999001"
    preview = str(rows[0].get("TG conversation") or "").lower()
    assert "maria_flowers_msk" in preview or "пишите" in preview

    # Stamp must refresh a stale TG conversation column, not only empty ones.
    rows[0]["TG conversation"] = "устаревший preview"
    rows[0]["tg_conversation"] = "устаревший preview"
    stamped2 = stamp_catalog_rows_from_overlay(rows)
    assert stamped2 >= 1
    refreshed = str(rows[0].get("TG conversation") or "").lower()
    assert "устаревший" not in refreshed
    assert "maria_flowers_msk" in refreshed or "пишите" in refreshed or "букет" in refreshed

    result = ensure_export_imported(rows)
    assert result["stamped_rows"] >= 0
    assert result.get("cache_backend") in {"file", "redis+file"}


def test_overlay_writes_redis_when_available(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/15")
    clear_import_memory_for_tests()
    clear_memory_for_tests()

    fake = MagicMock()
    fake.ping.return_value = True
    store: dict[str, str] = {}

    def _setex(key, ttl, value):
        store[key] = value
        return True

    def _get(key):
        return store.get(key)

    fake.setex.side_effect = _setex
    fake.get.side_effect = _get

    monkeypatch.setattr(
        "plugins.moysklad.telegram_export._redis_client",
        lambda: fake,
    )
    monkeypatch.setattr(
        "plugins.moysklad.conversations._redis_client",
        lambda: fake,
    )

    export_path = _mini_export(tmp_path)
    result = import_export_into_catalog(
        [
            {
                "_moysklad_id": "cp-maria",
                "Наименование": "Мария Букет",
                "Телефон": "+7 (900) 555-11-22",
                "ТГ ник": "",
            }
        ],
        export_path=export_path,
        force=True,
    )
    assert result["ok"] is True
    assert result["cache_backend"] == "redis+file"
    assert any("telegram_export:overlay" in k for k in store)
    assert any("conversations:v1" in k for k in store)


def test_import_skipped_without_export(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("MOYSKLAD_TELEGRAM_EXPORT", raising=False)
    clear_import_memory_for_tests()
    clear_memory_for_tests()
    # No export file in HERMES_HOME / cwd data
    result = import_export_into_catalog(
        [{"_moysklad_id": "x", "Наименование": "A", "Телефон": "9001112233"}],
        export_path=tmp_path / "missing.json",
        force=True,
    )
    assert result["ok"] is False
    assert result["error"] == "export_not_found"
