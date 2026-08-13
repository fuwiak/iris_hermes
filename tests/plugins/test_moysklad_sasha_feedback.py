"""Regression suite for Саша лизинг feedback (Клиенты + ИИ + Рассылки).

Behavioral contracts only — no catalog snapshots / change-detectors.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from plugins.moysklad.classify import _row_matches_query, clients_page
from plugins.moysklad.client_card import (
    _AI_SYSTEM,
    _facts_payload,
    _order_public,
    build_client_detail,
    generate_ai_for_detail,
)
from plugins.moysklad.order_status import classify_order_payment, summarize_order_context
from plugins.moysklad.sales_channels import (
    NO_CHANNEL_LABEL,
    channel_category,
    channel_name_from_order,
    display_channel_label,
    format_channels_display,
    matches_marketplace_channel_name,
    refresh_row_channel_fields,
    resolve_channel_name,
    row_matches_marketplace_audience,
    row_matches_sales_filter,
    sales_channel_type_from_channels,
)


# ── helpers ───────────────────────────────────────────────────────────────


def _client_row(
    *,
    cid: str,
    name: str = "",
    phone: str = "",
    orders: list[dict[str, Any]] | None = None,
    tg: str = "",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    ctx = list(orders or [])
    row: dict[str, Any] = {
        "_moysklad_id": cid,
        "Наименование": name,
        "Телефон": phone,
        "email": "",
        "E-mail": "",
        "ТГ ник": tg,
        "TG conversation": "",
        "_moysklad_tags": list(tags or []),
        "_moysklad_tags_display": ", ".join(tags or []),
        "Группы": ", ".join(tags or []),
        "_orders_context": ctx,
        "order_count": len(ctx),
        "Всего заказов": len(ctx),
    }
    refresh_row_channel_fields(row)
    return row


def _catalog(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "rows": rows,
        "counts": {
            "total": len(rows),
            "direct": sum(1 for r in rows if row_matches_sales_filter(r, "direct")),
            "marketplace": sum(
                1 for r in rows if row_matches_sales_filter(r, "marketplace")
            ),
        },
        "orders_scanned": sum(len(r.get("_orders_context") or []) for r in rows),
        "counterparties_scanned": len(rows),
        "counterparties_deduped": len(rows),
    }


class _DummyClient:
    pass


# ═══════════════════════════════════════════════════════════════════════════
# Клиенты §1 — поиск + фильтры
# ═══════════════════════════════════════════════════════════════════════════


class TestClientsSearchAndFilters:
    def test_phone_search_normalizes_formats(self) -> None:
        row = _client_row(cid="p1", name="Саша", phone="+7 (919) 787-51-13")
        assert _row_matches_query(row, "+79197875113")
        assert _row_matches_query(row, "79197875113")
        assert _row_matches_query(row, "9197875113")
        assert _row_matches_query(row, "саша")
        assert not _row_matches_query(row, "0000000000")

    def test_search_finds_marketplace_client_under_direct_tab(self) -> None:
        """Search must ignore sales-tab scoping (default «Прямые» felt broken)."""
        direct = _client_row(
            cid="d1",
            name="Прямой",
            phone="+79991112233",
            orders=[
                {
                    "id": "o1",
                    "Канал продаж": "Telegram",
                    "channel": "Telegram",
                    "sum": 1000,
                    "payment_status": "paid",
                }
            ],
        )
        mp = _client_row(
            cid="m1",
            name="Маркет",
            phone="+7 (919) 787-51-13",
            orders=[
                {
                    "id": "o2",
                    "Канал продаж": "Flowwow Skyloft",
                    "channel": "Flowwow Skyloft",
                    "sum": 2000,
                    "payment_status": "paid",
                }
            ],
        )
        page = clients_page(
            _DummyClient(),  # type: ignore[arg-type]
            sales_filter="direct",
            q="79197875113",
            catalog=_catalog([direct, mp]),
        )
        ids = {c["id"] for c in page["clients"]}
        assert "m1" in ids

    def test_sales_filter_tabs_partition_without_search(self) -> None:
        direct = _client_row(
            cid="d1",
            name="TG",
            orders=[{"id": "1", "Канал продаж": "Telegram", "channel": "Telegram", "sum": 1}],
        )
        mp = _client_row(
            cid="m1",
            name="FW",
            orders=[
                {
                    "id": "2",
                    "Канал продаж": "Flowwow Skyloft",
                    "channel": "Flowwow Skyloft",
                    "sum": 1,
                }
            ],
        )
        cat = _catalog([direct, mp])
        d_page = clients_page(
            _DummyClient(),  # type: ignore[arg-type]
            sales_filter="direct",
            catalog=cat,
        )
        m_page = clients_page(
            _DummyClient(),  # type: ignore[arg-type]
            sales_filter="marketplace",
            catalog=cat,
        )
        assert {c["id"] for c in d_page["clients"]} == {"d1"}
        assert {c["id"] for c in m_page["clients"]} == {"m1"}


# ═══════════════════════════════════════════════════════════════════════════
# Клиенты §2 + §3a — архивный Flowwow Skyloft
# ═══════════════════════════════════════════════════════════════════════════


class TestArchivedSalesChannel:
    def test_flowwow_skyloft_is_marketplace(self) -> None:
        assert matches_marketplace_channel_name("Flowwow Skyloft")
        assert matches_marketplace_channel_name("  FLOWWOW   skyloft ")
        assert channel_category("Flowwow Skyloft") == "marketplace"
        assert sales_channel_type_from_channels(["Flowwow Skyloft"]) == "маркетплейс"
        row = _client_row(
            cid="sky",
            name="Клиент",
            phone="+79197875113",
            orders=[
                {
                    "id": "o1",
                    "Канал продаж": "Flowwow Skyloft",
                    "channel": "Flowwow Skyloft",
                    "sum": 5000,
                    "payment_status": "paid",
                }
            ],
        )
        assert row_matches_marketplace_audience(row) is True

    def test_archived_channel_resolved_by_id_lookup(self) -> None:
        order = {
            "salesChannel": {
                "meta": {
                    "href": (
                        "https://api.moysklad.ru/api/remap/1.2/"
                        "entity/saleschannel/sky-archived"
                    )
                }
            }
        }
        # Expand often omits name for archived refs — directory must supply it.
        assert (
            channel_name_from_order(order, {"sky-archived": "Flowwow Skyloft"})
            == "Flowwow Skyloft"
        )

    def test_archived_channel_resolved_via_get_by_id(self) -> None:
        order = {
            "salesChannel": {
                "id": "sky-archived",
                "meta": {
                    "href": (
                        "https://api.moysklad.ru/api/remap/1.2/"
                        "entity/saleschannel/sky-archived"
                    )
                },
            }
        }
        by_id: dict[str, str] = {}

        def fetch(cid: str) -> dict[str, Any]:
            assert cid == "sky-archived"
            return {"id": cid, "name": "Flowwow Skyloft", "archived": True}

        assert resolve_channel_name(order, by_id, fetch_channel=fetch) == "Flowwow Skyloft"
        assert by_id["sky-archived"] == "Flowwow Skyloft"

    def test_phone_79197875113_shows_archived_channel_not_bez_kanala(self) -> None:
        row = _client_row(
            cid="c7919",
            name="Клиент 7919",
            phone="+7 (919) 787-51-13",
            orders=[
                {
                    "id": "o1",
                    "name": "00042",
                    "moment": "2025-08-01",
                    "sum": 4500,
                    "Канал продаж": "Flowwow Skyloft",
                    "channel": "Flowwow Skyloft",
                    "state": "Доставлен",
                    "payment_status": "paid",
                }
            ],
        )
        detail = build_client_detail(row)
        assert "Flowwow Skyloft" in (detail["client"].get("channel") or "")
        assert detail["client"]["channel"] != NO_CHANNEL_LABEL
        assert detail["orders"][0]["channel"] == "Flowwow Skyloft"

    def test_missing_channel_only_then_bez_kanala(self) -> None:
        assert display_channel_label("") == NO_CHANNEL_LABEL
        assert format_channels_display([]) == "Без канала"
        assert channel_name_from_order({}, {}) is None


# ═══════════════════════════════════════════════════════════════════════════
# Клиенты §3b — отменённые заказы + статус из МойСклад
# ═══════════════════════════════════════════════════════════════════════════


class TestCancelledOrdersAndStatus:
    def test_cancelled_order_kept_with_moysklad_status(self) -> None:
        row = _client_row(
            cid="c7919",
            name="Клиент",
            phone="+79197875113",
            orders=[
                {
                    "id": "paid1",
                    "name": "0001",
                    "moment": "2025-07-01",
                    "sum": 3000,
                    "payed_sum": 3000,
                    "unpaid": 0,
                    "channel": "Flowwow Skyloft",
                    "state": "Доставлен",
                    "applicable": True,
                },
                {
                    "id": "cancel1",
                    "name": "0002",
                    "moment": "2025-08-01",
                    "sum": 5000,
                    "payed_sum": 0,
                    "unpaid": 5000,
                    "channel": "Flowwow Skyloft",
                    "state": "Отменен",
                    "applicable": False,
                },
            ],
        )
        # Stamp payment_status like ingest does.
        for o in row["_orders_context"]:
            o["payment_status"] = classify_order_payment(o)

        detail = build_client_detail(row)
        assert len(detail["orders"]) == 2
        cancelled = [o for o in detail["orders"] if o.get("payment_status") == "cancelled"]
        assert len(cancelled) == 1
        assert cancelled[0]["state"] == "Отменен"
        assert cancelled[0]["name"] == "0002"

        # Public order payload must expose status fields for the UI badges.
        pub = _order_public(row["_orders_context"][1])
        assert pub["payment_status"] == "cancelled"
        assert pub["state"] == "Отменен"

    def test_facts_payload_includes_order_status_for_ai(self) -> None:
        detail = build_client_detail(
            _client_row(
                cid="x",
                name="X",
                orders=[
                    {
                        "id": "c1",
                        "moment": "2025-08-01",
                        "sum": 1000,
                        "state": "Отменен",
                        "applicable": False,
                        "payment_status": "cancelled",
                        "channel": "Telegram",
                    }
                ],
            )
        )
        facts = _facts_payload(detail)
        assert facts["orders"]
        assert facts["orders"][0].get("payment_status") == "cancelled"
        assert facts["orders"][0].get("state") == "Отменен"


# ═══════════════════════════════════════════════════════════════════════════
# Клиенты §3c — кол-во заказов (включая отменённые)
# ═══════════════════════════════════════════════════════════════════════════


class TestOrderCount:
    def test_order_count_includes_cancelled_and_paid(self) -> None:
        """Дмитрий-style: total orders = all API rows; paid/cancelled broken out."""
        orders = [
            {
                "id": f"o{i}",
                "moment": f"2025-0{i}-01",
                "sum": 2000,
                "payed_sum": 2000 if i < 4 else 0,
                "unpaid": 0 if i < 4 else 2000,
                "state": "Доставлен" if i < 4 else "Отменен",
                "applicable": i < 4,
                "channel": "Витрина",
            }
            for i in range(1, 5)
        ]
        for o in orders:
            o["payment_status"] = classify_order_payment(o)
        summary = summarize_order_context(orders)
        assert summary["order_count"] == 4
        assert summary["paid_order_count"] == 3
        assert summary["cancelled_order_count"] == 1
        assert summary["fulfilled_order_count"] == 3

        row = _client_row(cid="dmitry", name="Дмитрий Врублевский", orders=orders)
        detail = build_client_detail(row)
        assert detail["stats"]["order_count"] == 4
        assert detail["stats"]["paid_order_count"] == 3
        assert detail["stats"]["cancelled_order_count"] == 1
        assert len(detail["orders"]) == 4


# ═══════════════════════════════════════════════════════════════════════════
# ИИ §d — GPT / DeepSeek provider+model
# ═══════════════════════════════════════════════════════════════════════════


class TestAiModelChoice:
    def test_generate_ai_passes_provider_and_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        class _Resp:
            choices = [MagicMock(message=MagicMock(content='{"history_profile":"h","occasion_intent":"o","recommendation":"r"}'))]

        def fake_llm(**kwargs):
            captured.update(kwargs)
            return _Resp()

        monkeypatch.setattr(
            "agent.auxiliary_client.call_llm", fake_llm, raising=True
        )
        monkeypatch.setattr(
            "agent.auxiliary_client.extract_content_or_reasoning",
            lambda r: r.choices[0].message.content,
            raising=True,
        )

        detail = build_client_detail(
            _client_row(
                cid="ai1",
                name="Клиент",
                phone="+7999",
                orders=[
                    {
                        "id": "o1",
                        "moment": "2025-03-01",
                        "sum": 3000,
                        "channel": "Telegram",
                        "payment_status": "paid",
                    }
                ],
            )
        )
        out = generate_ai_for_detail(
            detail,
            provider="openrouter",
            model="deepseek/deepseek-chat",
        )
        assert captured.get("provider") == "openrouter"
        assert captured.get("model") == "deepseek/deepseek-chat"
        assert out["source"] == "llm"
        assert out["provider"] == "openrouter"
        assert out["model"] == "deepseek/deepseek-chat"

        # Empty override → DeepSeek default.
        out_default = generate_ai_for_detail(detail)
        assert out_default["provider"] == "openrouter"
        assert out_default["model"] == "deepseek/deepseek-chat"
        assert captured.get("model") == "deepseek/deepseek-chat"


# ═══════════════════════════════════════════════════════════════════════════
# ИИ §e — Telegram переписка учитывается (анатолий)
# ═══════════════════════════════════════════════════════════════════════════


class TestAiUsesTelegramConversation:
    def test_ai_system_requires_tg_when_present(self) -> None:
        low = _AI_SYSTEM.lower()
        assert "conversation" in low or "переписк" in low
        assert "telegram" in low or "тг" in low or "tg_" in low

    def test_facts_and_generate_include_tg_thread(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured_user = {"text": ""}

        class _Resp:
            choices = [
                MagicMock(
                    message=MagicMock(
                        content=(
                            '{"history_profile":"учёл чат",'
                            '"occasion_intent":"o",'
                            '"recommendation":"r"}'
                        )
                    )
                )
            ]

        def fake_llm(**kwargs):
            msgs = kwargs.get("messages") or []
            for m in msgs:
                if m.get("role") == "user":
                    captured_user["text"] = m.get("content") or ""
            return _Resp()

        monkeypatch.setattr("agent.auxiliary_client.call_llm", fake_llm, raising=True)
        monkeypatch.setattr(
            "agent.auxiliary_client.extract_content_or_reasoning",
            lambda r: r.choices[0].message.content,
            raising=True,
        )

        detail = build_client_detail(
            _client_row(
                cid="anatoly",
                name="анатолий",
                phone="+79001112233",
                tg="@anatoly",
                orders=[
                    {
                        "id": "o1",
                        "moment": "2025-05-01",
                        "sum": 4000,
                        "channel": "Telegram",
                        "payment_status": "paid",
                    }
                ],
            )
        )
        detail["client"]["tg_conversation"] = "https://t.me/anatoly"
        detail["conversation"] = {
            "message_count": 2,
            "preview": "нужен букет к субботе",
            "messages": [
                {
                    "direction": "inbound",
                    "text": "нужен букет к субботе",
                    "ts": "2025-05-01T10:00:00Z",
                },
                {
                    "direction": "outbound",
                    "text": "конечно, какие цветы?",
                    "ts": "2025-05-01T10:05:00Z",
                },
            ],
        }

        facts = _facts_payload(detail)
        assert facts["conversation"]["message_count"] == 2
        assert facts["conversation"]["messages"]

        out = generate_ai_for_detail(detail, provider="openrouter", model="deepseek/deepseek-chat")
        assert out["source"] == "llm"
        blob = captured_user["text"].lower()
        assert "conversation_preview" in blob or "нужен букет" in blob
        assert "telegram" in blob or "tg_nick" in blob or "@anatoly" in blob

    def test_get_thread_does_not_soft_match_client_name(self, tmp_path, monkeypatch) -> None:
        """Common first names must not steal another client's TG thread."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        from plugins.moysklad import conversations as conv

        conv._MEMORY_STORE = None
        conv._MEMORY_FP = None
        with conv._LOCK:
            store = conv._empty_store()
            store["threads"]["orphan-anatoly"] = {
                "client_id": "orphan-anatoly",
                "client_name": "анатолий",
                "phone": "",
                "tg_nick": "",
                "messages": [
                    {
                        "id": "1",
                        "direction": "inbound",
                        "channel": "telegram",
                        "text": "привет из тг",
                        "ts": "2025-01-01T00:00:00Z",
                        "source": "telegram_export",
                    }
                ],
                "created_at": "2025-01-01T00:00:00Z",
                "updated_at": "2025-01-01T00:00:00Z",
            }
            conv._save(store)

        thread = conv.get_thread(client_id="other-id", client_name="Анатолий")
        assert thread.get("empty") is True
        assert not thread.get("messages")


# ═══════════════════════════════════════════════════════════════════════════
# Рассылки §1–2 — фильтры + поиск аудитории (тот же /clients API)
# ═══════════════════════════════════════════════════════════════════════════


class TestOutreachAudienceFilters:
    def test_audience_all_filter_lists_multiple_clients(self) -> None:
        rows = [
            _client_row(
                cid=f"c{i}",
                name=f"Клиент {i}",
                phone=f"+7999000000{i}",
                orders=[
                    {
                        "id": f"o{i}",
                        "Канал продаж": "Telegram" if i % 2 == 0 else "Flowwow Skyloft",
                        "channel": "Telegram" if i % 2 == 0 else "Flowwow Skyloft",
                        "sum": 1000,
                        "payment_status": "paid",
                    }
                ],
            )
            for i in range(5)
        ]
        page = clients_page(
            _DummyClient(),  # type: ignore[arg-type]
            sales_filter="all",
            limit=40,
            catalog=_catalog(rows),
        )
        assert page["matched_total"] == 5
        assert len(page["clients"]) == 5

    def test_audience_search_by_phone_and_name(self) -> None:
        rows = [
            _client_row(cid="a", name="Алексей", phone="+7 (900) 111-22-33"),
            _client_row(cid="b", name="Борис", phone="+7 (900) 444-55-66"),
        ]
        by_phone = clients_page(
            _DummyClient(),  # type: ignore[arg-type]
            sales_filter="all",
            q="9001112233",
            catalog=_catalog(rows),
        )
        assert {c["id"] for c in by_phone["clients"]} == {"a"}

        by_name = clients_page(
            _DummyClient(),  # type: ignore[arg-type]
            sales_filter="all",
            q="борис",
            catalog=_catalog(rows),
        )
        assert {c["id"] for c in by_name["clients"]} == {"b"}


# ═══════════════════════════════════════════════════════════════════════════
# Рассылки §3 — Telegram login: phone normalize + logout clears session
# ═══════════════════════════════════════════════════════════════════════════


class TestTelegramAccountReconnect:
    def test_normalize_login_phone_accepts_common_formats(self) -> None:
        from plugins.platforms.telegram_user.client import normalize_login_phone

        assert normalize_login_phone("+7 (999) 123-45-67").startswith("+")
        assert "9991234567" in normalize_login_phone("89991234567").replace("+", "")
        assert normalize_login_phone("+79991234567")

    def test_logout_clears_session_so_phone_login_can_restart(
        self, tmp_path, monkeypatch
    ) -> None:
        from plugins.platforms.telegram_user import client as tu

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.delenv("TELEGRAM_USER_GATEWAY_URL", raising=False)
        for key in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_USER_SESSION"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("TELEGRAM_BUILTIN_API", "0")
        tu._save_config(
            {
                "session": "s" * 40,
                "user": {"id": 1, "username": "me"},
                "phone": "+79991234567",
            }
        )
        # Avoid live Telethon: stub the log_out path.
        monkeypatch.setattr(tu, "session_string", lambda: "s" * 40)
        monkeypatch.setattr(tu, "_call", lambda *_a, **_k: {"ok": True})
        monkeypatch.setattr(tu._RUNNER, "reset", lambda: None)

        res = tu.logout()
        assert res.get("ok") is True
        cfg = tu.load_config()
        assert not cfg.get("session")
        assert not cfg.get("user")

        # After logout, start_login must accept a phone again (validation path).
        monkeypatch.setattr(tu, "session_string", lambda: "")
        monkeypatch.setattr(tu, "api_credentials", lambda: ("12345", "hash" * 8))
        monkeypatch.setattr(tu, "_gateway_base", lambda: "")

        def _fake_call(factory, timeout=0):
            return {
                "ok": True,
                "authorized": False,
                "code_sent": True,
                "phone_code_hash": "hash123",
            }

        monkeypatch.setattr(tu, "_call", _fake_call)
        monkeypatch.setattr(tu._RUNNER, "reset", lambda: None)

        login = tu.start_login(phone="+79990001122")
        assert login.get("ok") is True
        assert login.get("code_sent") is True or login.get("authorized") is True
