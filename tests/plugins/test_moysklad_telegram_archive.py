"""ТГ архив — every export chat stays visible, matched or not."""

from __future__ import annotations

import json
from pathlib import Path

from plugins.moysklad.conversations import clear_memory_for_tests
from plugins.moysklad.telegram_archive import (
    archive_contacts,
    clear_memory_for_tests as clear_archive_memory,
    find_peer,
    get_chat,
    list_chats,
    rebuild,
)
from plugins.moysklad.telegram_export import clear_import_memory_for_tests, fold_name


def _chat(name: str, chat_id: int, texts: list[tuple[str, str]]) -> dict:
    """``texts`` — (from_id, body) pairs; ``user111`` is the studio side."""
    return {
        "name": name,
        "type": "personal_chat",
        "id": chat_id,
        "messages": [
            {
                "id": i + 1,
                "type": "message",
                "date": f"2026-03-0{i + 1}T10:00:00",
                "from": name,
                "from_id": from_id,
                "text": body,
                "text_entities": [{"type": "plain", "text": body}],
            }
            for i, (from_id, body) in enumerate(texts)
        ],
    }


def _export(tmp: Path) -> Path:
    payload = {
        "personal_information": {"user_id": 111},
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
                _chat(
                    "Мария Букет",
                    999001,
                    [("user999001", "Хочу букет"), ("user111", "Добрый день!")],
                ),
                # Latin «с» in a Cyrillic name — the classic unmatchable title.
                _chat(
                    "Viсtoria",
                    999002,
                    [("user999002", "Здравствуйте"), ("user111", "Оформим")],
                ),
                # No card at all — must still be readable and reachable.
                _chat("Незнакомец", 999003, [("user999003", "Привет")]),
                _chat(
                    "Аноним",
                    999004,
                    [("user999004", "Мой телефон +7 900 777-22-33")],
                ),
            ]
        },
    }
    path = tmp / "telegram_export.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _rows() -> list[dict]:
    return [
        {
            "_moysklad_id": "cp-maria",
            "Наименование": "Мария Букет",
            "Телефон": "+7 (900) 555-11-22",
            "ТГ ник": "",
        },
        {
            "_moysklad_id": "cp-victoria",
            "Наименование": "Victoria",  # all-Latin card
            "Телефон": "",
            "ТГ ник": "",
        },
        {
            "_moysklad_id": "cp-phone",
            "Наименование": "Клиент без имени",
            "Телефон": "+7 900 777-22-33",
            "ТГ ник": "",
        },
    ]


def test_fold_name_collapses_lookalike_letters():
    assert fold_name("Viсtoria") == fold_name("Victoria")
    assert fold_name(" Мария  Букет ") == fold_name("мария букет")


def test_archive_indexes_every_chat_matched_or_not(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    clear_import_memory_for_tests()
    clear_memory_for_tests()
    clear_archive_memory()
    export_path = _export(tmp_path)

    stats = rebuild(_rows(), export_path=export_path, force=True)
    assert stats["ok"] is True
    assert stats["chats_total"] == 4
    # phone (Мария) + folded name (Viсtoria) + phone typed in chat (Аноним)
    assert stats["matched"] == 3
    assert stats["unmatched"] == 1

    page = list_chats()
    assert page["counts"] == {"total": 4, "matched": 3, "unmatched": 1}
    by_name = {c["name"]: c for c in page["chats"]}
    assert by_name["Viсtoria"]["client_id"] == "cp-victoria"
    assert by_name["Аноним"]["client_id"] == "cp-phone"
    assert by_name["Незнакомец"]["client_id"] == ""
    assert by_name["Незнакомец"]["chat_id"] == "999003"


def test_unmatched_chat_is_readable_and_sendable(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    clear_import_memory_for_tests()
    clear_memory_for_tests()
    clear_archive_memory()
    export_path = _export(tmp_path)
    rebuild(_rows(), export_path=export_path, force=True)

    chat = get_chat("999003")
    assert chat["ok"] is True
    assert chat["conversation"]["message_count"] == 1
    assert "Привет" in chat["conversation"]["messages"][0]["text"]

    # Numeric peer id is exactly what Business sendMessage needs.
    peer = find_peer(tg_chat_id="999003")
    assert peer is not None and peer["tg_chat_id"] == "999003"

    contacts = archive_contacts(unmatched_only=True)
    ids = {c["id"] for c in contacts}
    assert "tg:999003" in ids
    assert "tg:999001" not in ids  # matched chats come from the catalog instead


def test_search_finds_chat_by_lookalike_name(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    clear_import_memory_for_tests()
    clear_memory_for_tests()
    clear_archive_memory()
    export_path = _export(tmp_path)
    rebuild(_rows(), export_path=export_path, force=True)

    hits = list_chats(q="Victoria")
    assert [c["name"] for c in hits["chats"]] == ["Viсtoria"]

    unmatched = list_chats(state="unmatched")
    assert [c["name"] for c in unmatched["chats"]] == ["Незнакомец"]


def test_index_survives_missing_export(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("MOYSKLAD_TELEGRAM_EXPORT", raising=False)
    clear_import_memory_for_tests()
    clear_memory_for_tests()
    clear_archive_memory()
    export_path = _export(tmp_path)
    rebuild(_rows(), export_path=export_path, force=True)

    export_path.unlink()
    clear_archive_memory()
    page = list_chats()
    assert page["counts"]["total"] == 4
