"""Навык генерации: skills store + few-shot block + chat refine."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    yield


def test_save_list_delete_and_prompt_block():
    from plugins.moysklad import outreach_skills as sk

    a = sk.save_skill(text="Привет! Ваши пионы ждут вас 🌸")
    sk.save_skill(text="Добрый день! Напомню про накопленные баллы.")
    items = sk.list_skills()
    assert len(items) == 2
    block = sk.prompt_examples_block()
    assert "пионы" in block and "баллы" in block
    # Duplicate text refreshes, not duplicates.
    sk.save_skill(text="Привет! Ваши пионы ждут вас 🌸")
    assert len(sk.list_skills()) == 2
    assert sk.delete_skill(a["id"]) is False  # old id gone after refresh
    remaining = sk.list_skills()
    assert sk.delete_skill(remaining[0]["id"]) is True
    assert len(sk.list_skills()) == 1


def test_skills_flow_into_outreach_system_prompt():
    from plugins.moysklad import outreach_skills as sk
    from plugins.moysklad.outreach import _OUTREACH_SYSTEM

    sk.save_skill(text="Фирменный тёплый тон без канцелярита.")
    system = _OUTREACH_SYSTEM("Анна", "")
    assert "Фирменный тёплый тон" in system
    assert "навык" in system.lower()


def test_chat_refine_returns_reply_and_message(monkeypatch):
    import agent.auxiliary_client as aux
    from plugins.moysklad.outreach import chat_refine_message

    captured = {}

    def _fake_call_llm(**kwargs):
        captured["model"] = kwargs.get("model")
        captured["messages"] = kwargs.get("messages")

        class _R:
            pass

        return _R()

    monkeypatch.setattr(aux, "call_llm", _fake_call_llm)
    monkeypatch.setattr(
        aux,
        "extract_content_or_reasoning",
        lambda _r: '{"reply": "Сделал короче", "message": "Привет, Мария! Пионы ждут."}',
    )

    detail = {
        "client": {"id": "c1", "name": "Мария", "loyalty_points": 120},
        "orders": [],
    }
    out = chat_refine_message(
        detail,
        channel="telegram",
        draft="Старый длинный текст",
        chat=[{"role": "user", "content": "короче"}],
        provider="openrouter",
        model="x-ai/grok-3",
    )
    assert out["ok"] is True
    assert out["reply"] == "Сделал короче"
    assert out["message"].startswith("Привет, Мария")
    assert captured["model"] == "x-ai/grok-3"
    roles = [m["role"] for m in captured["messages"]]
    assert roles[0] == "system" and "user" in roles


def test_sent_log_survives_thread_trim(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from plugins.moysklad.conversations import append_message, clear_memory_for_tests
    from plugins.moysklad.sent_history import list_sent_messages

    clear_memory_for_tests()
    append_message(
        client_id="c-log",
        text="Логируемое исходящее",
        direction="outbound",
        client_name="Клиент",
        source="client_card_send",
    )
    rows = list_sent_messages(limit=10)
    assert any(r["text"] == "Логируемое исходящее" for r in rows)
    # No duplicate from log ∪ conversations merge.
    assert sum(1 for r in rows if r["text"] == "Логируемое исходящее") == 1
