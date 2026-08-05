"""Tests for MoySklad outreach draft grounding + campaign client sync fields."""

from __future__ import annotations

import plugins.moysklad.campaigns as campaigns
from plugins.moysklad.client_card import build_client_detail
from plugins.moysklad.outreach import (
    _OUTREACH_SYSTEM,
    facts_panel,
    heuristic_outreach_message,
    heuristic_sanity_check,
    rewrite_outreach_message,
    _parse_outreach_json,
)


def _sample_row(**overrides):
    base = {
        "_moysklad_id": "cp-out-1",
        "Наименование": "Мария Букет",
        "Телефон": "+7 (900) 555-11-22",
        "E-mail": "maria@example.com",
        "_moysklad_tags": ["#постоянный", "событие марта"],
        "_moysklad_state": "активный",
        "Тип канала продаж": "Прямые",
        "Канал продаж": "WhatsApp",
        "Баллы начисленные": "40",
        "ТГ ник": "",
        "order_count": 2,
        "avg_check": 3800.0,
        "last_order_at": "2025-03-01 11:00:00",
        "_audience": {"direct": True, "marketplace": False},
        "_orders_context": [
            {
                "id": "o1",
                "name": "100",
                "moment": "2025-03-01 11:00:00",
                "sum": 4000,
                "channel": "WhatsApp",
                "product_snippet": "Пионы",
            },
            {
                "id": "o2",
                "name": "101",
                "moment": "2024-02-10 09:00:00",
                "sum": 3600,
                "channel": "Telegram",
                "product_snippet": "Розы",
            },
        ],
    }
    base.update(overrides)
    return base


def test_facts_panel_exposes_audit_fields():
    detail = build_client_detail(_sample_row())
    panel = facts_panel(detail)
    assert panel["name"] == "Мария Букет"
    assert panel["order_count"] == 2
    assert panel["avg_check"] == 3800.0
    assert panel["phone"]
    assert len(panel["orders_preview"]) >= 1
    assert panel["recommendation"]
    # Three structured audit blocks (not AI prose alone)
    assert panel["block_history_profile"]["title"] == "История и профиль"
    assert panel["block_occasion_intent"]["title"] == "Повод и intent"
    assert panel["block_risks"]["title"] == "Риски / ограничения"
    assert panel["block_history_profile"]["empty"] is False
    assert panel["risks"]["has_debt"] is False
    assert panel["risks"]["do_not_upsell"] is False


def test_facts_panel_never_invents_debt():
    detail = build_client_detail(_sample_row())
    panel = facts_panel(detail)
    assert panel["risks"]["balance"] is None or panel["risks"]["balance"] >= 0
    assert panel["risks"]["has_debt"] is False
    assert panel["block_risks"]["do_not_upsell"] is False
    # Empty-risk note or non-debt lines only — never a fake долг amount
    blob = " ".join(
        f"{x.get('label')} {x.get('value')}"
        for x in (panel["block_risks"].get("lines") or [])
    )
    assert "долг по балансу" not in blob.lower() or panel["risks"]["has_debt"]


def test_debt_blocks_flower_upsell_heuristic():
    row = _sample_row(balance=-12500.0)
    detail = build_client_detail(row)
    assert detail["risks"]["has_debt"] is True
    assert detail["risks"]["do_not_upsell"] is True
    out = heuristic_outreach_message(detail, channel="telegram", seller_name="Анна")
    low = out["message"].lower()
    assert "премиум" not in low
    assert "пион" not in low and "роз" not in low
    assert "оплат" in low or "свер" in low
    # Reminder must itself pass the upsell heuristic.
    assert heuristic_sanity_check(out["message"], detail, seller_name="Анна")["ok"]
    panel = out["facts"]
    assert panel["block_risks"]["do_not_upsell"] is True
    assert any(
        "долг" in (line.get("label") or "").lower()
        or "долг" in (line.get("value") or "").lower()
        for line in panel["block_risks"]["lines"]
    )


def test_heuristic_sanity_rejects_flower_upsell_on_debt():
    row = _sample_row(balance=-5000.0)
    detail = build_client_detail(row)
    bad = (
        "Здравствуйте, Мария! Это Анна. Подберём премиум-букет из роз "
        "к вашему поводу — напишите, забронируем."
    )
    sanity = heuristic_sanity_check(bad, detail, seller_name="Анна")
    assert sanity["ok"] is False
    assert sanity["issues"]
    assert sanity["revised_text"]
    rev = sanity["revised_text"].lower()
    assert "премиум" not in rev
    assert "оплат" in rev or "свер" in rev
    assert heuristic_sanity_check(sanity["revised_text"], detail, seller_name="Анна")[
        "ok"
    ]

    ok_msg = (
        "Здравствуйте, Мария! Это Анна. Хотели мягко свериться по оплате — "
        "напишите, когда удобно закрыть вопрос."
    )
    ok = heuristic_sanity_check(ok_msg, detail, seller_name="Анна")
    assert ok["ok"] is True
    assert not ok["issues"]


def test_heuristic_sanity_rejects_gift_upsell_on_debt():
    row = _sample_row(balance=-5000.0)
    detail = build_client_detail(row)
    bad = (
        "Здравствуйте, Мария! Это Анна. Хотим сделать вам подарок и скидку "
        "на следующий букет — напишите!"
    )
    sanity = heuristic_sanity_check(bad, detail, seller_name="Анна")
    assert sanity["ok"] is False
    assert any("долг" in i.lower() or "оплат" in i.lower() for i in sanity["issues"])
    rev = (sanity["revised_text"] or "").lower()
    assert "подарок" not in rev
    assert "скидк" not in rev
    assert "оплат" in rev or "свер" in rev


def test_outreach_temperatures_are_creative_and_sales():
    from plugins.moysklad.outreach import (
        OUTREACH_GENERATE_TEMPERATURE,
        OUTREACH_REWRITE_TEMPERATURE,
        OUTREACH_SANITY_TEMPERATURE,
    )

    assert OUTREACH_GENERATE_TEMPERATURE >= 0.8
    assert OUTREACH_REWRITE_TEMPERATURE >= 0.9
    assert OUTREACH_SANITY_TEMPERATURE <= 0.2
    assert OUTREACH_REWRITE_TEMPERATURE > OUTREACH_GENERATE_TEMPERATURE


def test_outreach_system_prompts_cover_debt_and_creativity():
    from plugins.moysklad.outreach import _REWRITE_SYSTEM, _SANITY_SYSTEM

    prompt = _OUTREACH_SYSTEM("Анна", "")
    assert "КРЕАТИВНЫЙ" in prompt or "креатив" in prompt.lower()
    assert "долг" in prompt.lower()
    assert "подар" in prompt.lower()
    assert "продающ" in _REWRITE_SYSTEM.lower()
    assert "здрав" in _SANITY_SYSTEM.lower() or "долг" in _SANITY_SYSTEM.lower()
    assert "подар" in _SANITY_SYSTEM.lower()


def test_heuristic_outreach_cites_facts_not_discounts():
    detail = build_client_detail(_sample_row())
    out = heuristic_outreach_message(
        detail,
        channel="whatsapp",
        seller_name="Анна из Iris",
        seller_facts="Доставка по городу, акцент на сезонные букеты",
    )
    msg = out["message"]
    low = msg.lower()
    assert "анна" in low or "iris" in low
    assert "2025-03-01" not in msg
    assert "пион" in low
    assert "скидк" not in low
    assert "промокод" not in low
    assert "навязан" not in low
    assert "(whatsapp)" not in low
    assert "-50%" not in msg
    assert out["source"] == "heuristic"
    assert out["facts"]["client_id"]
    assert out["seller_name"] == "Анна из Iris"


def test_heuristic_skips_internal_order_codes():
    row = _sample_row()
    row["_orders_context"] = [
        {
            "id": "o1",
            "name": "1605-02",
            "moment": "2026-05-16 12:00:00",
            "sum": 10790,
            "channel": "Telegram",
            "product_snippet": "1605-02",
        }
    ]
    row["avg_check"] = 10790.0
    row["last_order_at"] = "2026-05-16 12:00:00"
    detail = build_client_detail(row)
    out = heuristic_outreach_message(detail, channel="telegram", seller_name="Анна")
    msg = out["message"]
    assert "1605-02" not in msg
    assert "2026-05-16" not in msg
    assert "мая" in msg.lower() or "помог" in msg.lower()
    assert "(whatsapp)" not in msg.lower()
    assert "(telegram)" not in msg.lower()


def test_heuristic_outreach_thin_data_avoids_fake_history():
    row = _sample_row()
    row["_orders_context"] = []
    row["order_count"] = 0
    row["avg_check"] = 0
    row["Телефон"] = ""
    detail = build_client_detail(row)
    out = heuristic_outreach_message(
        detail, channel="telegram", seller_name="Магазин Роза"
    )
    low = out["message"].lower()
    assert "скидк" not in low
    assert "vip" not in low or "не" in low
    assert "роза" in low or "магазин" in low
    assert out["facts"]["data_thin"] is True


def test_outreach_system_prompt_includes_seller_fields():
    prompt = _OUTREACH_SYSTEM("Анна из Iris", "Адрес: ул. Цветочная 1")
    assert "Анна из Iris" in prompt
    assert "ул. Цветочная 1" in prompt
    assert "Это Iris" in prompt  # instruction: don't hardcode unless signature says so
    assert "навязанных скидок" in prompt
    assert "КРЕАТИВНЫЙ" in prompt or "креатив" in prompt.lower()
    assert "подар" in prompt.lower()


def test_rewrite_heuristic_removes_robot_meta():
    draft = (
        "Здравствуйте, Анатолий! Это Iris. Последний заказ у нас был 2026-05-16 "
        "(1605-02). Ориентир по прошлым заказам ≈ 10790 ₽. Напишите, если удобно "
        "продолжить подбор — без навязанных скидок, только по вашей истории. (WhatsApp)"
    )
    out = rewrite_outreach_message(
        draft,
        channel="telegram",
        seller_name="Анна из Iris",
    )
    # Without LLM this path is heuristic_rewrite (or llm if available)
    msg = out["message"]
    low = msg.lower()
    assert "навязан" not in low
    assert "только по вашей истории" not in low
    assert "1605-02" not in msg
    assert "(whatsapp)" not in low
    assert out["source"] in ("heuristic_rewrite", "llm_rewrite")


def test_parse_outreach_json():
    parsed = _parse_outreach_json(
        '```json\n{"message":"Привет","grounding_notes":"даты заказов"}\n```'
    )
    assert parsed == {"message": "Привет", "grounding_notes": "даты заказов"}


def test_seller_settings_persist(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    saved = campaigns.save_seller_settings(
        seller_name="Анна из Iris",
        seller_facts="Сезонные букеты, доставка",
    )
    assert saved["seller_name"] == "Анна из Iris"
    loaded = campaigns.get_seller_settings()
    assert loaded["seller_facts"] == "Сезонные букеты, доставка"


def test_create_draft_stores_client_facts(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    detail = build_client_detail(_sample_row())
    panel = facts_panel(detail)
    item = campaigns.create_draft(
        title="Черновик · Мария",
        channel="whatsapp",
        mode="auto",
        offer="Здравствуйте, Мария!",
        sales_filter="direct",
        audience_count=1,
        client_id="cp-out-1",
        client_name="Мария Букет",
        facts=panel,
        recommendation=panel.get("recommendation") or "",
        grounding_notes="только факты",
        ai_source="heuristic",
    )
    listed = campaigns.list_campaigns()
    assert listed[0]["client_id"] == "cp-out-1"
    assert listed[0]["facts"]["order_count"] == 2
    assert listed[0]["ai_source"] == "heuristic"
    assert item["offer"].startswith("Здравствуйте")


def test_outreach_generate_routes_llm_via_openrouter_egress(monkeypatch):
    """Regression: рассылки AI must use OPENROUTER_BASE_URL, not openrouter.ai.

    Chat already worked through Railway egress; generate/rewrite/sanity used
    auxiliary ``call_llm`` → ``_try_openrouter`` which dialed openrouter.ai
    (Selectel RU → HTTP 403 security policy).
    """
    from unittest.mock import MagicMock, patch

    from plugins.moysklad.outreach import generate_outreach_message

    proxy = "https://openrouter-egress-production.up.railway.app/t/secret/api/v1"
    monkeypatch.setenv("OPENROUTER_BASE_URL", proxy)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-outreach-test")

    captured: dict = {}
    llm_calls = {"n": 0}

    class _FakeMessage:
        content = (
            '{"message":"Здравствуйте, Мария! Пионы ждали вас.",'
            '"grounding_notes":"order пионы"}'
        )
        reasoning = None

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeResponse:
        choices = [_FakeChoice()]

    class _FakeCompletions:
        def create(self, **_kwargs):
            llm_calls["n"] += 1
            return _FakeResponse()

    class _FakeChat:
        completions = _FakeCompletions()

    def _fake_openai(**kwargs):
        captured["base_url"] = kwargs.get("base_url")
        captured["api_key"] = kwargs.get("api_key")
        client = MagicMock()
        client.chat = _FakeChat()
        client.base_url = kwargs.get("base_url")
        return client

    detail = build_client_detail(_sample_row())

    with patch("agent.auxiliary_client._select_pool_entry", return_value=(False, None)), \
         patch("agent.auxiliary_client.OpenAI", side_effect=_fake_openai):
        result = generate_outreach_message(
            detail,
            channel="telegram",
            refresh_ai=True,
            seller_name="Анна",
            seller_facts="цветы",
        )

    assert captured.get("base_url") == proxy
    assert "openrouter.ai" not in str(captured.get("base_url") or "")
    # One message LLM only — not card AI + message + sanity (was 3 serial calls).
    assert llm_calls["n"] == 1
    assert result.get("source") == "llm"
    assert (result.get("sanity") or {}).get("source") == "heuristic"
    assert "Мария" in (result.get("message") or "")
    assert result.get("error") is None or "403" not in str(result.get("error"))


def test_auto_sanity_after_generate_is_heuristic_not_llm(monkeypatch):
    """«Проверить смысл» uses LLM; auto post-generate sanity must not."""
    from plugins.moysklad.outreach import sanity_check_outreach_message

    detail = build_client_detail(_sample_row())
    called = {"llm": False}

    def _boom(*_a, **_k):
        called["llm"] = True
        raise AssertionError("LLM should not run for use_llm=False")

    monkeypatch.setattr(
        "agent.auxiliary_client.call_llm",
        _boom,
    )
    out = sanity_check_outreach_message(
        "Здравствуйте, Мария!",
        detail,
        use_llm=False,
    )
    assert out["source"] == "heuristic"
    assert called["llm"] is False


def test_progressive_json_message_streams_visible_text():
    from plugins.moysklad.outreach import ProgressiveJsonMessage

    p = ProgressiveJsonMessage()
    assert p.feed('{"message": "') == ""
    assert p.feed("Привет, ") == "Привет, "
    assert p.feed('Мария!\\nЖдём.", "grounding_notes": "x"}') == "Мария!\nЖдём."
    assert p.message == "Привет, Мария!\nЖдём."
    assert p._done is True


def test_iter_generate_outreach_events_streams_deltas(monkeypatch):
    from plugins.moysklad.outreach import iter_generate_outreach_events

    detail = build_client_detail(_sample_row())
    chunks = [
        '{"message": "',
        "Здравствуйте, Мария!",
        '", "grounding_notes": "пион"}',
    ]

    class _Delta:
        def __init__(self, content):
            self.content = content

    class _Choice:
        def __init__(self, content):
            self.delta = _Delta(content)

    class _Chunk:
        def __init__(self, content):
            self.choices = [_Choice(content)]

    def _fake_call_llm(**kwargs):
        assert kwargs.get("stream") is True
        return iter(_Chunk(c) for c in chunks)

    monkeypatch.setattr("agent.auxiliary_client.call_llm", _fake_call_llm)

    events = list(iter_generate_outreach_events(detail, channel="telegram", seller_name="Анна"))
    types = [e.get("type") for e in events]
    assert "status" in types
    assert "delta" in types
    assert types[-1] == "done"
    deltas = "".join(e["text"] for e in events if e.get("type") == "delta")
    assert "Мария" in deltas
    done = events[-1]
    assert done.get("source") == "llm"
    assert "Мария" in (done.get("message") or "")


def test_iter_personalize_batch_events_yields_per_client(monkeypatch):
    from plugins.moysklad.outreach import iter_personalize_batch_events

    monkeypatch.setattr(
        "plugins.moysklad.outreach.build_outreach_for_row",
        lambda row, **_kw: {
            "message": f"hi {row.get('name')}",
            "client_id": row.get("id"),
            "client_name": row.get("name"),
            "source": "heuristic",
            "grounding_notes": "",
        },
    )
    rows = [
        {"id": "a", "name": "Аня"},
        {"id": "b", "name": "Боря"},
    ]
    events = list(iter_personalize_batch_events(rows, channel="telegram", max_workers=2))
    assert events[0]["type"] == "batch_start"
    assert events[0]["total"] == 2
    client_dones = [e for e in events if e["type"] == "client_done"]
    assert len(client_dones) == 2
    names = {e["client_name"] for e in client_dones}
    assert names == {"Аня", "Боря"}
    assert events[-1]["type"] == "batch_done"
    assert events[-1]["ok_count"] == 2


def test_heuristic_bouquet_names_historical_product():
    from plugins.moysklad.outreach import heuristic_bouquet_suggestion

    detail = build_client_detail(_sample_row())
    out = heuristic_bouquet_suggestion(detail, channel="telegram", seller_name="Анна")
    assert out["source"] == "heuristic_bouquet"
    msg = out["message"] or ""
    assert "Пионы" in msg or "пионы" in msg.lower()
    assert out.get("bouquet")


def test_heuristic_paraphrase_differs_from_draft():
    from plugins.moysklad.outreach import _too_similar, heuristic_paraphrase

    draft = (
        "Здравствуйте, Мария! Это Анна. В прошлый раз у вас были пионы. "
        "Напишите, если удобно подобрать букет."
    )
    out = heuristic_paraphrase(draft, channel="telegram")
    assert out
    assert not _too_similar(out, draft, threshold=0.95)


def test_suggest_bouquet_routes_llm(monkeypatch):
    from unittest.mock import MagicMock, patch

    from plugins.moysklad.outreach import suggest_historical_bouquet_message

    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://egress.test/t/x/api/v1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")

    class _FakeMessage:
        content = (
            '{"message":"Здравствуйте, Мария! Это Анна. '
            'В прошлый раз у вас были Пионы — соберём снова?",'
            '"grounding_notes":"пионы"}'
        )
        reasoning = None

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeResponse:
        choices = [_FakeChoice()]

    class _FakeCompletions:
        def create(self, **_kwargs):
            return _FakeResponse()

    class _FakeChat:
        completions = _FakeCompletions()

    def _fake_openai(**kwargs):
        client = MagicMock()
        client.chat = _FakeChat()
        client.base_url = kwargs.get("base_url")
        return client

    detail = build_client_detail(_sample_row())
    with patch("agent.auxiliary_client._select_pool_entry", return_value=(False, None)), \
         patch("agent.auxiliary_client.OpenAI", side_effect=_fake_openai):
        result = suggest_historical_bouquet_message(
            detail, channel="telegram", seller_name="Анна"
        )
    assert result.get("source") == "llm_bouquet"
    assert "Пионы" in (result.get("message") or "")


def test_paraphrase_rejects_near_duplicate(monkeypatch):
    from plugins.moysklad.outreach import paraphrase_outreach_message

    draft = "Здравствуйте, Мария! Это Анна. Пионы ждали вас. Напишите."
    calls = {"n": 0}

    def _fake_call_llm(**_kwargs):
        calls["n"] += 1

        class _Msg:
            content = (
                '{"message":"Здравствуйте, Мария! Это Анна. Пионы ждали вас. Напишите.",'
                '"grounding_notes":"same"}'
            )
            reasoning = None

        class _Ch:
            message = _Msg()

        class _Resp:
            choices = [_Ch()]

        return _Resp()

    monkeypatch.setattr("agent.auxiliary_client.call_llm", _fake_call_llm)
    detail = build_client_detail(_sample_row())
    out = paraphrase_outreach_message(
        draft, channel="telegram", seller_name="Анна", detail=detail
    )
    assert calls["n"] >= 2  # retry once when too similar
    assert out.get("source") in ("heuristic_paraphrase", "llm_paraphrase")
    assert (out.get("message") or "").strip() != draft
