"""«Карточки» chat analyst: context prompt + LLM turn plumbing."""

from __future__ import annotations

import sys
import types

from plugins.moysklad.cards_chat import build_context_prompt, cards_chat_reply


def test_context_prompt_carries_report_and_missing_rule() -> None:
    prompt = build_context_prompt(
        {"2026-07": {"flowwow": {"turnover": 460502.0, "orders": 41}}},
        {"flowwow": {"cards_total": 518}},
    )
    assert "460502" in prompt
    assert "cards_total" in prompt
    assert "нет" in prompt  # «данных в МоемСкладе нет» rule


def _stub_llm(monkeypatch, reply_text: str, captured: dict) -> None:
    module = types.ModuleType("agent.auxiliary_client")

    def call_llm(**kwargs):
        captured.update(kwargs)
        return {"text": reply_text}

    def extract_content_or_reasoning(response):
        return response.get("text", "")

    module.call_llm = call_llm
    module.extract_content_or_reasoning = extract_content_or_reasoning
    monkeypatch.setitem(sys.modules, "agent.auxiliary_client", module)


def test_chat_reply_passes_history_and_context(monkeypatch) -> None:
    captured: dict = {}
    _stub_llm(monkeypatch, "Мне не хватает цифр с Яндекс Маркета за 2026-07.", captured)
    out = cards_chat_reply(
        [{"role": "user", "content": "Построй отчёт за июль"}],
        month_report={"2026-06": {"direct": {"turnover": 1000.0}}},
    )
    assert out["ok"] is True
    assert "не хватает" in out["reply"]
    messages = captured["messages"]
    assert messages[0]["role"] == "system"
    assert "НИКОГДА не выдумывай" in messages[0]["content"]
    assert "2026-06" in messages[1]["content"]
    assert messages[-1] == {"role": "user", "content": "Построй отчёт за июль"}


def test_chat_reply_empty_llm_is_error(monkeypatch) -> None:
    captured: dict = {}
    _stub_llm(monkeypatch, "", captured)
    out = cards_chat_reply([{"role": "user", "content": "?"}], month_report={})
    assert out["ok"] is False
    assert out["error"] == "empty_llm_response"


def test_cards_advisor_context_and_prompt(monkeypatch) -> None:
    from plugins.moysklad.cards_chat import cards_advisor_reply

    captured: dict = {}
    _stub_llm(monkeypatch, "Добавьте «Пионы» на Яндекс Маркет.", captured)
    combined = [
        {
            "name": "Пионы",
            "marketplaces": ["flowwow"],
            "listings": {"flowwow": {"is_active": True, "images_count": 2, "price": "8990.00"}},
        }
    ]
    out = cards_advisor_reply(
        [{"role": "user", "content": "Что улучшить в карточке Пионы?"}], combined=combined
    )
    assert out["ok"] is True
    messages = captured["messages"]
    system = messages[0]["content"]
    assert "размещение" in system.lower() or "продвижени" in system.lower()
    assert "Никаких заголовков" in system  # anti-slop format rules
    context = messages[1]["content"]
    # precomputed recommendations carry the card as an add-to-yandex candidate…
    assert "add_to_yandex" in context and "Пионы" in context
    # …and the retrieval block found it by the question tokens
    assert "Карточки, найденные по вопросу" in context
