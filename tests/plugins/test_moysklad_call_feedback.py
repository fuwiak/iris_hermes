"""Regression suite for the 15.08 call feedback (Саша ↔ Паша).

Functional contracts over real-shaped MoySklad data — no snapshot cheating:
1. Физ/юр chips filter real «Тип контрагента» labels through clients_page.
2. «Есть баллы» filter uses real «Баллы начисленные» values.
3. Chat refine actually rewrites the draft and honours the model choice.
4. TG verdicts: quota-throttled phone probes stay UNCHECKED, live threads win.
5. История отправок sorts mixed real timestamp formats newest-first and
   includes fresh single sends.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("MOYSKLAD_REDIS_URL", raising=False)
    monkeypatch.setenv("MOYSKLAD_API_TOKEN", "test-token")
    from plugins.moysklad import tg_verify
    from plugins.moysklad.conversations import clear_memory_for_tests

    clear_memory_for_tests()
    tg_verify._MEMORY = None
    tg_verify._MEMORY_FP = None
    yield
    clear_memory_for_tests()
    tg_verify._MEMORY = None
    tg_verify._MEMORY_FP = None


class _DummyClient:
    pass


def _row(cid: str, name: str, **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "_moysklad_id": cid,
        "Наименование": name,
        "Телефон": extra.pop("phone", ""),
        "ТГ ник": extra.pop("tg", ""),
        "_moysklad_tags": [],
        "_orders_context": [],
        "_audience": {"direct": True, "marketplace": False},
        "Тип канала продаж": "Прямые",
    }
    base.update(extra)
    return base


def _catalog(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "rows": rows,
        "counts": {"total": len(rows), "direct": len(rows), "marketplace": 0},
        "orders_scanned": 0,
        "counterparties_scanned": len(rows),
        "counterparties_deduped": len(rows),
    }


# ── 1+2: физ/юр и баллы, сквозь clients_page (как жмёт оператор) ─────────


ROWS = [
    _row("f1", "Ирина", **{"Тип контрагента": "Физическое лицо", "Баллы начисленные": "40"}),
    _row("f2", "Олег", **{"Тип контрагента": "Физическое лицо", "Баллы начисленные": ""}),
    _row("u1", "ООО Ромашка", **{"Тип контрагента": "Юридическое лицо", "Баллы начисленные": "0"}),
    _row("u2", "ИП Иванов", **{"Тип контрагента": "Индивидуальный предприниматель", "Баллы начисленные": "12.5"}),
    _row("x1", "Без типа", **{"Тип контрагента": ""}),
]


def test_entity_chips_partition_real_company_types():
    from plugins.moysklad.classify import clients_page

    fiz = clients_page(
        _DummyClient(), entity_type="individual", catalog=_catalog(ROWS)
    )
    assert {c["id"] for c in fiz["clients"]} == {"f1", "f2"}

    jur = clients_page(_DummyClient(), entity_type="legal", catalog=_catalog(ROWS))
    assert {c["id"] for c in jur["clients"]} == {"u1"}  # ИП — отдельная кнопка

    allp = clients_page(_DummyClient(), entity_type="all", catalog=_catalog(ROWS))
    assert allp["matched_total"] == 5


def test_loyalty_filter_shows_only_clients_with_points():
    from plugins.moysklad.classify import clients_page

    page = clients_page(
        _DummyClient(), loyalty_only=True, catalog=_catalog(ROWS)
    )
    assert {c["id"] for c in page["clients"]} == {"f1", "u2"}

    combo = clients_page(
        _DummyClient(),
        loyalty_only=True,
        entity_type="entrepreneur",
        catalog=_catalog(ROWS),
    )
    assert {c["id"] for c in combo["clients"]} == {"u2"}


# ── 3: чат реально переписывает текст выбранной моделью ──────────────────


def test_chat_rewrites_draft_and_uses_chosen_model(monkeypatch):
    import agent.auxiliary_client as aux
    from plugins.moysklad.outreach import chat_refine_message

    captured: dict[str, Any] = {}

    def _fake_call_llm(**kwargs):
        captured["model"] = kwargs.get("model")
        return object()

    monkeypatch.setattr(aux, "call_llm", _fake_call_llm)
    monkeypatch.setattr(
        aux,
        "extract_content_or_reasoning",
        lambda _r: (
            '{"reply": "Переписал свежими словами",'
            ' "message": "Ирина, привет! 🌸 Ваши 40 баллов ждут — потратьте их на новый букет."}'
        ),
    )

    detail = {
        "client": {"id": "f1", "name": "Ирина", "loyalty_points": 40},
        "orders": [],
    }
    draft = "Здравствуйте, Ирина. У вас есть баллы."
    out = chat_refine_message(
        detail,
        channel="telegram",
        draft=draft,
        chat=[{"role": "user", "content": "живее и про баллы"}],
        provider="openrouter",
        model="openai/gpt-5",
    )
    assert out["ok"] is True
    assert out["message"] != draft  # текст реально изменился
    assert "баллов" in out["message"] or "баллы" in out["message"]
    assert captured["model"] == "openai/gpt-5"


# ── 4: вердикты ТГ ────────────────────────────────────────────────────────


def test_quota_throttled_phone_probe_is_not_a_no_telegram_verdict(monkeypatch):
    """importContacts с исчерпанной квотой возвращает retry_contacts —
    клиент остаётся НЕ ПРОВЕРЕН, а не «нет Telegram»."""
    from plugins.moysklad import tg_verify
    from plugins.platforms.telegram_user import client as tg_user

    monkeypatch.setattr(tg_user, "is_authorized", lambda: True)
    monkeypatch.setattr(
        tg_user,
        "resolve_peer",
        lambda _q: {
            "ok": False,
            "error": "phone_check_throttled",
            "detail": "Лимит проверки номеров исчерпан — повторите позже",
        },
    )
    result = tg_verify.verify_client_peers(
        client_id="q1", phone="+7 982 235-21-88"
    )
    assert result["checked"] is False  # никакого вердикта
    assert result["active"] is False or result.get("active") is False
    assert "исчерпан" in str(result.get("detail") or "")


def test_live_thread_beats_any_probe(monkeypatch):
    from plugins.moysklad import tg_verify
    from plugins.moysklad.conversations import append_message

    append_message(
        client_id="h1",
        text="сиски",
        direction="inbound",
        tg_nick="@pawels2137",
        tg_chat_id="796461007",
        client_name="Hans",
        source="telegram_user",
    )
    result = tg_verify.verify_client_peers(client_id="h1", phone="+7 900 000-00-00")
    assert result["active"] is True
    assert result["checked"] is True
    assert result["via"] == "history"


def test_batch_verdicts_only_for_actually_checked_numbers(monkeypatch):
    """Egress returns checked without throttled numbers — those stay unchecked."""
    from plugins.moysklad import tg_verify
    from plugins.platforms.telegram_user import client as tg_user

    rows = [
        _row("p1", "Проверенный", phone="+7 900 111-22-33"),
        _row("p2", "Затротленный", phone="+7 900 222-33-44"),
    ]
    monkeypatch.setattr(
        tg_user,
        "resolve_phones_bulk",
        lambda phones: {
            "ok": True,
            "requested": len(phones),
            "checked": ["+79001112233"],  # p2 throttled by quota → not checked
            "found": {},
            "flood_wait": 0,
        },
    )
    stats = tg_verify.verify_rows_by_phone_bulk(rows)
    assert stats["checked"] == 1
    assert tg_verify.overlay_for_client("p1")["active"] is False
    assert not tg_verify.overlay_for_client("p2")  # без вердикта


# ── 5: История отправок — реальные форматы времени, свежие сверху ─────────


def test_sent_feed_sorts_mixed_real_timestamps_newest_first(tmp_path, monkeypatch):
    from plugins.moysklad.conversations import append_message
    from plugins.moysklad.sent_history import list_sent_messages, record_sent

    # Telegram-export era rows (RU format) + MoySklad-style + live ISO send.
    record_sent(
        {
            "client_id": "old-1",
            "client_name": "Экспортный",
            "text": "Старое из экспорта",
            "ts": "24.07.2026 12:00",
            "channel": "telegram",
            "source": "telegram_export",
        }
    )
    record_sent(
        {
            "client_id": "mid-1",
            "client_name": "Средний",
            "text": "Сообщение начала августа",
            "ts": "2026-08-02 10:00:00",
            "channel": "telegram",
            "source": "campaign_send",
        }
    )
    fresh = append_message(
        client_id="new-1",
        text="Свежая отправка сегодня",
        direction="outbound",
        client_name="Свежий",
        source="client_card_send",
    )
    assert fresh["message_count"] == 1

    rows = list_sent_messages(limit=50)
    texts = [r["text"] for r in rows]
    assert texts.index("Свежая отправка сегодня") < texts.index(
        "Сообщение начала августа"
    )
    assert texts.index("Сообщение начала августа") < texts.index(
        "Старое из экспорта"
    )
    # Свежая отправка — первая: «старьё 24 июля» больше не открывает список.
    assert rows[0]["text"] == "Свежая отправка сегодня"


def test_recency_epoch_parses_ru_export_dates():
    from plugins.moysklad.conversations import recency_epoch

    ru = recency_epoch("24.07.2026 12:00")
    iso = recency_epoch("2026-07-24T12:00:00+00:00")
    assert ru == iso == datetime(
        2026, 7, 24, 12, 0, tzinfo=timezone.utc
    ).timestamp()
    assert recency_epoch("2026-08-15T09:00:00+00:00") > ru
    assert recency_epoch("мусор") == float("-inf")


# ── модель и заземление даты (звонок: «не понимает, какое сегодня число») ──


def test_generate_prompt_carries_today_and_elapsed_days():
    from plugins.moysklad.outreach import _generate_user_prompt

    detail = {
        "client": {"id": "r1", "name": "Ростислав", "loyalty_points": None},
        "orders": [
            {
                "id": "o1",
                "date": "2026-06-14 12:33:00",
                "sum": 11490,
                "product_snippet": "дофаминовый букет",
            }
        ],
        "ai": {"recommendation": "тест"},
    }
    prompt = _generate_user_prompt(
        detail, channel="telegram", seller_name="Анна", seller_facts=""
    )
    today = datetime.now(timezone.utc).date().isoformat()
    assert f"Сегодня: {today}" in prompt
    assert '"days_since_last_order"' in prompt
    # Июньский заказ — прошло явно больше 30 дней; число попало в JSON фактов.
    import json as _json
    import re as _re

    m = _re.search(r'"days_since_last_order":\s*(\d+)', prompt)
    assert m and int(m.group(1)) > 30


def test_outreach_task_uses_card_quality_model():
    """Сообщения и карточка — одна модель (deepseek-chat), а не flash."""
    import inspect

    import plugins.moysklad as pkg

    src = inspect.getsource(pkg)
    assert '"model": "deepseek/deepseek-chat"' in src
    assert "deepseek-v4-flash" not in src


def test_outreach_system_prompt_bans_fake_recency():
    from plugins.moysklad.outreach import _OUTREACH_SYSTEM

    prompt = _OUTREACH_SYSTEM("Анна", "")
    assert "days_since_last_order" in prompt
    assert "до сих пор радует" in prompt  # явный запрет формулировки


def test_entity_filter_separates_ip_from_legal():
    """ИП — отдельная кнопка: «Индивидуальный предприниматель» больше не
    прячется в «Юр. лица», а «Юр. лица» показывает только юрлиц."""
    from plugins.moysklad.classify import clients_page

    ip = clients_page(
        _DummyClient(), entity_type="entrepreneur", catalog=_catalog(ROWS)
    )
    assert {c["id"] for c in ip["clients"]} == {"u2"}

    jur = clients_page(_DummyClient(), entity_type="legal", catalog=_catalog(ROWS))
    assert {c["id"] for c in jur["clients"]} == {"u1"}

    fiz = clients_page(
        _DummyClient(), entity_type="individual", catalog=_catalog(ROWS)
    )
    assert {c["id"] for c in fiz["clients"]} == {"f1", "f2"}

    # Три кнопки — раздел без пересечений; «без типа» виден только на «все».
    assert (
        len(ip["clients"]) + len(jur["clients"]) + len(fiz["clients"]) == 4
    )
