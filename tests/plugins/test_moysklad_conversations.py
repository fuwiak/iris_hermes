"""Tests for MoySklad local TG conversation store."""

from __future__ import annotations

from plugins.moysklad.client_card import build_client_detail
from plugins.moysklad.conversations import (
    append_message,
    clear_live_pull_throttle_for_tests,
    clear_memory_for_tests,
    enrich_client_row,
    get_thread,
    preview_text,
    seed_from_moysklad_attr,
)
from plugins.moysklad.outreach import facts_panel


def test_append_outbound_and_preview(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    clear_memory_for_tests()
    thread = append_message(
        client_id="cp-1",
        text="Здравствуйте! Сверимся по оплате.",
        direction="outbound",
        channel="telegram",
        phone="+7 (900) 111-22-33",
        tg_nick="@maria",
        client_name="Мария",
        source="campaign_send",
    )
    assert thread["message_count"] == 1
    assert thread["messages"][0]["direction"] == "outbound"
    assert "исходящее" in thread["messages"][0]["label"].lower()
    assert "telegram" in thread["messages"][0]["label"].lower()
    prev = preview_text(thread)
    assert "оплат" in prev.lower()

    # Lookup by phone / nick
    by_phone = get_thread(phone="79001112233")
    assert by_phone["message_count"] == 1
    by_nick = get_thread(tg_nick="maria")
    assert by_nick["message_count"] == 1


def test_inbound_append_same_thread(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    clear_memory_for_tests()
    append_message(
        client_id="cp-2",
        text="Исходящее",
        direction="outbound",
        channel="whatsapp",
    )
    thread = append_message(
        client_id="cp-2",
        text="Ок, переведу завтра",
        direction="inbound",
        channel="whatsapp",
    )
    assert thread["message_count"] == 2
    assert thread["messages"][-1]["direction"] == "inbound"
    assert "входящее" in thread["messages"][-1]["label"].lower()


def test_sync_from_telegram_user_merges_inbound(tmp_path, monkeypatch):
    """Personal MTProto history must land as inbound/outbound in the thread."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    clear_memory_for_tests()

    def _fake_history(*, peer: str, limit: int = 40):
        assert "@maria" in peer or "maria" in peer
        return {
            "ok": True,
            "tg_chat_id": "4242",
            "tg_nick": "maria",
            "messages": [
                {
                    "direction": "outbound",
                    "text": "Здравствуйте!",
                    "ts": "2026-08-10T10:00:00+00:00",
                    "message_id": 1,
                },
                {
                    "direction": "inbound",
                    "text": "Да, давайте букет к пятнице",
                    "ts": "2026-08-10T10:05:00+00:00",
                    "message_id": 2,
                },
            ],
            "via": "stub",
        }

    import plugins.platforms.telegram_user.client as tg_user

    monkeypatch.setattr(tg_user, "fetch_history", _fake_history)

    from plugins.moysklad.conversations import sync_from_telegram_user

    thread = sync_from_telegram_user(
        client_id="cp-tg",
        tg_nick="@maria",
        client_name="Мария",
    )
    assert thread["message_count"] == 2
    assert thread["sync"]["imported"] == 2
    assert thread["sync"]["inbound_imported"] == 1
    assert any(m["direction"] == "inbound" for m in thread["messages"])
    # Idempotent
    again = sync_from_telegram_user(client_id="cp-tg", tg_nick="@maria")
    assert again["sync"]["imported"] == 0
    assert again["message_count"] == 2


def test_conversation_for_detail_pulls_inbound(tmp_path, monkeypatch):
    """Selecting a client for Facts must pull personal-TG replies into history."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    clear_memory_for_tests()
    clear_live_pull_throttle_for_tests()

    append_message(
        client_id="cp-facts",
        text="Рассылка: букет к пятнице?",
        direction="outbound",
        tg_nick="@buyer",
        tg_chat_id="777",
        source="campaign_send",
    )

    import plugins.platforms.telegram_user.client as tg_user

    monkeypatch.setattr(
        tg_user,
        "fetch_history",
        lambda *, peer, limit=40: {
            "ok": True,
            "tg_chat_id": "777",
            "tg_nick": "buyer",
            "messages": [
                {
                    "direction": "outbound",
                    "text": "Рассылка: букет к пятнице?",
                    "ts": "2026-08-10T10:00:00+00:00",
                    "message_id": 1,
                },
                {
                    "direction": "inbound",
                    "text": "Да, давайте розы",
                    "ts": "2026-08-10T11:00:00+00:00",
                    "message_id": 2,
                },
            ],
        },
    )

    from plugins.moysklad.conversations import conversation_for_detail

    detail = {
        "client": {
            "id": "cp-facts",
            "name": "Buyer",
            "tg_nick": "@buyer",
            "tg_chat_id": "777",
        }
    }
    thread = conversation_for_detail(detail, pull_live=True, force=True)
    assert thread["message_count"] >= 2
    assert any(m.get("direction") == "inbound" for m in thread["messages"])
    assert int((thread.get("sync") or {}).get("inbound_imported") or 0) >= 1


def test_sync_client_conversation_combines_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    clear_memory_for_tests()

    from plugins.moysklad import conversations as conv

    def _fake_gateway(**kwargs):
        return {
            **conv.get_thread(client_id=kwargs["client_id"]),
            "sync": {
                "ok": True,
                "imported": 0,
                "inbound_imported": 0,
                "matched_sessions": 0,
                "source": "gateway_telegram",
            },
        }

    def _fake_user(**kwargs):
        append_message(
            client_id=kwargs["client_id"],
            text="Входящее с личного TG",
            direction="inbound",
            tg_nick=kwargs.get("tg_nick") or "",
            source="telegram_user",
        )
        public = conv.get_thread(client_id=kwargs["client_id"])
        public["sync"] = {
            "ok": True,
            "imported": 1,
            "inbound_imported": 1,
            "source": "telegram_user",
        }
        return public

    monkeypatch.setattr(conv, "sync_from_gateway", _fake_gateway)
    monkeypatch.setattr(conv, "sync_from_telegram_user", _fake_user)

    thread = conv.sync_client_conversation(client_id="cp-mix", tg_nick="@x")
    assert thread["sync"]["inbound_imported"] == 1
    assert thread["message_count"] >= 1


def test_seed_from_attr_once(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    clear_memory_for_tests()
    first = seed_from_moysklad_attr(
        client_id="cp-3",
        attr_value="Клиент писал: нужна доставка к пятнице",
        phone="79001110000",
    )
    # Attr-only notes are not a Telegram chat — public view stays empty.
    assert first["empty"] is True
    assert first.get("attr_only_ghost") is True
    second = seed_from_moysklad_attr(
        client_id="cp-3",
        attr_value="другой текст не должен дублировать",
        phone="79001110000",
    )
    assert second["empty"] is True
    from plugins.moysklad import conversations as conv

    with conv._LOCK:
        store = conv._load()
        raw = store["threads"]["cp-3"]["messages"]
    assert len(raw) == 1
    assert raw[0]["source"] == "moysklad_attr"
    assert "доставка" in raw[0]["text"]


def test_url_attr_not_seeded_as_message(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    clear_memory_for_tests()
    thread = seed_from_moysklad_attr(
        client_id="cp-4",
        attr_value="https://t.me/c/1/2",
    )
    assert thread["empty"] is True


def test_export_preview_attr_not_seeded_or_shown(tmp_path, monkeypatch):
    """Stolen export snippets must not become fake TG history on another card."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    clear_memory_for_tests()
    ghost = (
        "[исходящее · Telegram · export] Оформили возврат по заказу, "
        "деньги вернутся в течение 3–10 дней"
    )
    thread = seed_from_moysklad_attr(
        client_id="0095f2bc-ghost",
        attr_value=ghost,
        phone="+79686889933",
        client_name="Александр",
    )
    assert thread["empty"] is True
    assert thread["message_count"] == 0

    # Pre-existing attr-only store entry is hidden from public API.
    from plugins.moysklad import conversations as conv

    with conv._LOCK:
        store = conv._load()
        store["threads"]["0095f2bc-ghost"] = {
            "client_id": "0095f2bc-ghost",
            "client_name": "Александр",
            "phone": "79686889933",
            "tg_nick": "",
            "messages": [
                {
                    "id": "g1",
                    "direction": "system",
                    "channel": "telegram",
                    "text": ghost,
                    "ts": "2026-08-01T00:00:00Z",
                    "source": "moysklad_attr",
                }
            ],
            "created_at": "2026-08-01T00:00:00Z",
            "updated_at": "2026-08-01T00:00:00Z",
        }
        store["index"]["id:0095f2bc-ghost"] = "0095f2bc-ghost"
        store["index"]["phone:79686889933"] = "0095f2bc-ghost"
        conv._save(store)

    public = get_thread(client_id="0095f2bc-ghost", phone="+79686889933")
    assert public["empty"] is True
    assert public.get("attr_only_ghost") is True
    assert public["preview"] == ""

    row = enrich_client_row(
        {
            "id": "0095f2bc-ghost",
            "name": "Александр",
            "phone": "+79686889933",
            "tg_conversation": ghost,
        }
    )
    assert row["conversation_count"] == 0
    assert row["tg_conversation_preview"] == ""
    assert row["tg_conversation"] == ""

    purged = conv.purge_attr_only_ghost_threads()
    assert purged["threads_removed"] >= 1
    assert get_thread(client_id="0095f2bc-ghost")["empty"] is True


def test_enrich_and_facts_include_conversation(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    clear_memory_for_tests()
    append_message(
        client_id="cp-out-1",
        text="Черновик для фактов",
        direction="outbound",
        channel="telegram",
        phone="+7 (900) 555-11-22",
    )
    row = {
        "_moysklad_id": "cp-out-1",
        "Наименование": "Мария Букет",
        "Телефон": "+7 (900) 555-11-22",
        "_moysklad_tags": [],
        "order_count": 1,
        "avg_check": 1000.0,
        "_orders_context": [
            {
                "id": "o1",
                "moment": "2025-03-01 11:00:00",
                "sum": 1000,
                "channel": "Telegram",
                "product_snippet": "Розы",
            }
        ],
        "_audience": {"direct": True, "marketplace": False},
    }
    detail = build_client_detail(row)
    assert detail["conversation"]["message_count"] >= 1
    panel = facts_panel(detail)
    assert panel["conversation"]["message_count"] >= 1
    assert panel["conversation"]["messages"]
    public = enrich_client_row(
        {
            "id": "cp-out-1",
            "name": "Мария",
            "phone": "+7 (900) 555-11-22",
            "tg_conversation": "",
        }
    )
    assert public["tg_conversation_preview"]
    assert "Черновик" in public["tg_conversation"]


def test_list_awaiting_replies_after_mass_send(tmp_path, monkeypatch):
    """Mass-send cohort: only clients who spoke last show up for follow-up."""
    from plugins.moysklad.conversations import list_awaiting_replies

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    clear_memory_for_tests()

    append_message(
        client_id="sent-ok",
        text="Акция на букет",
        direction="outbound",
        client_name="Аня",
        source="campaign_send_batch",
    )
    append_message(
        client_id="replied",
        text="Акция на букет",
        direction="outbound",
        client_name="Боря",
        source="campaign_send_batch",
    )
    append_message(
        client_id="replied",
        text="Интересно, сколько стоит?",
        direction="inbound",
        client_name="Боря",
        source="telegram_user",
    )

    waiting = list_awaiting_replies(["sent-ok", "replied", "ghost"])
    assert [r["client_id"] for r in waiting] == ["replied"]
    assert waiting[0]["awaiting_reply"] is True
    assert "стоит" in (waiting[0]["preview"] or "").lower()

    empty = list_awaiting_replies(["sent-ok"])
    assert empty == []


def test_history_import_adds_answer_without_duplicating_sends(tmp_path, monkeypatch):
    """send() stamps _now(); Telegram history carries msg.date seconds later —
    the first successful history pull must add ONLY the client's answer."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    clear_memory_for_tests()

    from datetime import datetime, timedelta, timezone

    sent_at = datetime.now(timezone.utc)
    thread = append_message(
        client_id="cp-dup",
        text="Привет, Hans! Какой у вас любимый цветок?",
        direction="outbound",
        channel="telegram",
        tg_nick="@pawels2137",
        client_name="Hans",
        source="campaign_send",
    )
    assert thread["message_count"] == 1

    def _fake_history(*, peer: str, limit: int = 40):
        return {
            "ok": True,
            "tg_chat_id": "777",
            "tg_nick": "pawels2137",
            "messages": [
                {
                    # Same send, Telegram-stamped a few seconds later.
                    "direction": "outbound",
                    "text": "Привет, Hans! Какой у вас любимый цветок?",
                    "ts": (sent_at + timedelta(seconds=7)).isoformat(),
                    "message_id": 101,
                },
                {
                    "direction": "inbound",
                    "text": "сиски",
                    "ts": (sent_at + timedelta(seconds=40)).isoformat(),
                    "message_id": 102,
                },
            ],
            "via": "stub",
        }

    import plugins.platforms.telegram_user.client as tg_user

    monkeypatch.setattr(tg_user, "fetch_history", _fake_history)

    from plugins.moysklad.conversations import sync_from_telegram_user

    synced = sync_from_telegram_user(client_id="cp-dup", tg_nick="@pawels2137")
    assert synced["sync"]["imported"] == 1
    assert synced["sync"]["inbound_imported"] == 1
    assert synced["message_count"] == 2
    assert synced["messages"][-1]["direction"] == "inbound"
    assert synced["messages"][-1]["text"] == "сиски"

    # Repeat pull stays idempotent (message_id + fuzzy window both hold).
    again = sync_from_telegram_user(client_id="cp-dup", tg_nick="@pawels2137")
    assert again["sync"]["imported"] == 0
    assert again["message_count"] == 2


def test_history_import_keeps_genuine_rapid_repeats(tmp_path, monkeypatch):
    """Two identical client messages with distinct message_id both survive."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    clear_memory_for_tests()

    def _fake_history(*, peer: str, limit: int = 40):
        return {
            "ok": True,
            "tg_chat_id": "778",
            "tg_nick": "maria",
            "messages": [
                {
                    "direction": "inbound",
                    "text": "да",
                    "ts": "2026-08-13T09:51:00+00:00",
                    "message_id": 201,
                },
                {
                    "direction": "inbound",
                    "text": "да",
                    "ts": "2026-08-13T09:51:30+00:00",
                    "message_id": 202,
                },
            ],
            "via": "stub",
        }

    import plugins.platforms.telegram_user.client as tg_user

    monkeypatch.setattr(tg_user, "fetch_history", _fake_history)

    from plugins.moysklad.conversations import sync_from_telegram_user

    synced = sync_from_telegram_user(client_id="cp-rep", tg_nick="@maria")
    assert synced["sync"]["imported"] == 2
    assert synced["message_count"] == 2


def test_sync_client_conversation_falls_back_to_thread_peer(tmp_path, monkeypatch):
    """Outreach contact (custom:…) has no catalog row — the sync must reuse
    the peer stored on the thread instead of failing no_tg_nick_or_phone."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    clear_memory_for_tests()
    clear_live_pull_throttle_for_tests()

    append_message(
        client_id="custom:hans1",
        text="Привет, Hans! Какой у вас любимый цветок?",
        direction="outbound",
        channel="telegram",
        tg_nick="pawels2137",
        tg_chat_id="796461007",
        client_name="Hans",
        source="campaign_send",
    )

    seen_peer = {}

    def _fake_history(*, peer: str, limit: int = 40):
        seen_peer["peer"] = peer
        return {
            "ok": True,
            "tg_chat_id": "796461007",
            "tg_nick": "pawels2137",
            "messages": [
                {
                    "direction": "inbound",
                    "text": "сиски",
                    "ts": "2026-08-13T06:51:40+00:00",
                    "message_id": 501,
                },
            ],
            "via": "stub",
        }

    import plugins.platforms.telegram_user.client as tg_user

    monkeypatch.setattr(tg_user, "fetch_history", _fake_history)

    from plugins.moysklad.conversations import sync_client_conversation

    # Caller knows only the id — exactly what /conversation/sync sends for
    # contacts without a catalog row.
    thread = sync_client_conversation(client_id="custom:hans1")
    assert seen_peer["peer"] == "796461007"
    assert thread["sync"]["inbound_imported"] == 1
    assert thread["message_count"] == 2
    assert any(m["direction"] == "inbound" for m in thread["messages"])


def test_sync_falls_back_to_nick_when_chat_id_entity_unknown(tmp_path, monkeypatch):
    """Telethon can't resolve a bare numeric id until the entity is cached —
    retry with @nick instead of giving up (prod: PeerUser(796461007))."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    clear_memory_for_tests()

    tried = []

    def _fake_history(*, peer: str, limit: int = 40):
        tried.append(peer)
        if peer == "796461007":
            return {
                "ok": False,
                "error": "history_failed",
                "detail": "Could not find the input entity for PeerUser(796461007)",
            }
        assert peer == "@pawels2137"
        return {
            "ok": True,
            "tg_chat_id": "796461007",
            "tg_nick": "pawels2137",
            "messages": [
                {
                    "direction": "inbound",
                    "text": "сиски",
                    "ts": "2026-08-13T06:52:00+00:00",
                    "message_id": 601,
                },
            ],
            "via": "stub",
        }

    import plugins.platforms.telegram_user.client as tg_user

    monkeypatch.setattr(tg_user, "fetch_history", _fake_history)

    from plugins.moysklad.conversations import sync_from_telegram_user

    thread = sync_from_telegram_user(
        client_id="cp-fallback",
        tg_nick="pawels2137",
        tg_chat_id="796461007",
        client_name="Hans",
    )
    assert tried == ["796461007", "@pawels2137"]
    assert thread["sync"]["ok"] is True
    assert thread["sync"]["inbound_imported"] == 1


def test_outbound_blasts_group_identical_texts(tmp_path, monkeypatch):
    """История отправок: identical исходящие (≥2 clients) become a blast."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    clear_memory_for_tests()
    from plugins.moysklad.conversations import (
        get_outbound_blast,
        is_blast_history_id,
        list_outbound_blasts,
    )

    text = "Сезон пионов открыт!"
    for i, cid in enumerate(("c1", "c2", "c3")):
        append_message(
            client_id=cid,
            text=text,
            direction="outbound",
            channel="telegram",
            client_name=f"Client {i}",
            source="telegram_export",
        )
    # Unique 1:1 chatter must not appear as a blast.
    append_message(
        client_id="solo",
        text="Личное сообщение только одному",
        direction="outbound",
        source="telegram_export",
    )

    blasts = list_outbound_blasts(limit=10)
    assert len(blasts) == 1
    job = blasts[0]
    assert job["total"] == 3
    assert job["sent_ok"] == 3
    assert job["status"] == "done"
    assert job["history_kind"] == "conversation_blast"
    assert "пионов" in (job.get("message_preview") or "")
    assert is_blast_history_id(str(job["id"]))

    snap = get_outbound_blast(str(job["id"]), limit=10)
    assert snap is not None
    assert snap["results_total"] == 3
    assert len(snap["recipients"]) == 3
    assert {r["client_id"] for r in snap["recipients"]} == {"c1", "c2", "c3"}


def test_outbound_blasts_newest_day_first(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    clear_memory_for_tests()
    import json
    from hermes_constants import get_hermes_home
    from plugins.moysklad.conversations import get_outbound_blast, list_outbound_blasts

    root = get_hermes_home() / "moysklad"
    root.mkdir(parents=True, exist_ok=True)
    store = {
        "threads": {
            "c1": {
                "client_id": "c1",
                "client_name": "A",
                "messages": [
                    {
                        "direction": "outbound",
                        "text": "старая пачка",
                        "ts": "2020-01-01T10:00:00+00:00",
                        "source": "telegram_export",
                    }
                ],
            },
            "c2": {
                "client_id": "c2",
                "client_name": "B",
                "messages": [
                    {
                        "direction": "outbound",
                        "text": "старая пачка",
                        "ts": "2020-01-01T10:01:00+00:00",
                        "source": "telegram_export",
                    }
                ],
            },
            "c3": {
                "client_id": "c3",
                "client_name": "C",
                "messages": [
                    {
                        "direction": "outbound",
                        "text": "свежая пачка",
                        "ts": "2026-08-15T10:00:00+00:00",
                        "source": "telegram_export",
                    }
                ],
            },
            "c4": {
                "client_id": "c4",
                "client_name": "D",
                "messages": [
                    {
                        "direction": "outbound",
                        "text": "свежая пачка",
                        "ts": "2026-08-15T10:01:00+00:00",
                        "source": "telegram_export",
                    }
                ],
            },
        },
        "index": {},
    }
    (root / "conversations.json").write_text(
        json.dumps(store, ensure_ascii=False), encoding="utf-8"
    )
    blasts = list_outbound_blasts(limit=10)
    assert len(blasts) == 2
    assert "свежая" in (blasts[0].get("message_preview") or "")
    assert "старая" in (blasts[1].get("message_preview") or "")
    snap = get_outbound_blast(str(blasts[0]["id"]), limit=10)
    assert snap is not None
    assert [r["client_id"] for r in snap["recipients"]] == ["c4", "c3"]

