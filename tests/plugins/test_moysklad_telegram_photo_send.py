"""Отправка сообщения с фото конкретному получателю (@pawels2137).

Проверяет весь путь «ник → chat_id → sendPhoto»: именно он ломался тише
всего, потому что текстовая отправка продолжала работать, а фото молча
уходило в другой метод Bot API.

``send_outreach_to_client`` шлёт текст отдельным сообщением, а прикреплённые
фото — следом за ним (подпись в 1024 символа не вмещает продающий черновик с
несколькими карточками). Подпись и её обрезка живут уровнем ниже, в
``send_telegram_message``, и проверяются там же.

По умолчанию всё замокано и ничего наружу не уходит. Реальная отправка —
только с MOYSKLAD_TG_PHOTO_LIVE=1 и настоящим токеном (см. хвост файла).
"""

from __future__ import annotations

import base64
import os

import pytest

import plugins.moysklad.telegram_send as tg

RECIPIENT_NICK = "@pawels2137"
PHOTO_BYTES = b"\xff\xd8\xff\xe0-jpeg-fixture"
PHOTO_B64 = base64.b64encode(PHOTO_BYTES).decode()
CAPTION = "❣️ Букет для любимой из красных французских роз в стильной упаковке"


@pytest.fixture
def bot_env(monkeypatch):
    """Pin the Business-bot path — фото ходит только через него."""
    for key in (
        "TELEGRAM_BUSINESS_CONNECTION_ID",
        "MOYSKLAD_TELEGRAM_BUSINESS_CONNECTION_ID",
        "TELEGRAM_BUSINESS_BOT_USERNAME",
        "MOYSKLAD_TELEGRAM_BOT_USERNAME",
        "TELEGRAM_BOT_USERNAME",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TELEGRAM_BUSINESS_BOT_TOKEN", "1:TESTTOKEN")
    monkeypatch.setenv("MOYSKLAD_TELEGRAM_SEND_VIA", "bot")
    monkeypatch.setattr(tg, "_download_image_url", lambda *a, **k: None)


@pytest.fixture
def api_calls(monkeypatch):
    calls: list[dict] = []

    def fake_api(method, *, token, json_body=None, files=None, timeout=0, **kw):
        calls.append(
            {
                "method": method,
                "token": token,
                "json_body": dict(json_body or {}),
                "files": files,
            }
        )
        return {"ok": True, "result": {"message_id": 4242, "chat": {"id": 987654321}}}

    monkeypatch.setattr(tg, "telegram_api", fake_api)
    return calls


def test_nick_resolves_to_recipient_chat_id():
    assert tg.resolve_telegram_chat_id(tg_nick=RECIPIENT_NICK) == RECIPIENT_NICK
    # Без «собачки» и в другом регистре — тот же адресат.
    assert tg.resolve_telegram_chat_id(tg_nick="PaweLS2137") == "@pawels2137"
    assert (
        tg.resolve_telegram_chat_id(tg_conversation="https://t.me/pawels2137")
        == RECIPIENT_NICK
    )


def test_send_uploaded_photo_to_recipient(bot_env, api_calls):
    """Текст уходит сообщением, фото — следом, без подписи."""
    out = tg.send_outreach_to_client(
        text=CAPTION,
        tg_nick=RECIPIENT_NICK,
        image_base64=PHOTO_B64,
        image_name="bouquet.jpg",
    )

    assert out["ok"] is True
    assert out["photo_via"] == "business_bot_photo"
    assert out["photos_sent"] == 1

    assert [c["method"] for c in api_calls] == ["sendMessage", "sendPhoto"]
    assert api_calls[0]["json_body"]["text"] == CAPTION
    photo = api_calls[1]
    assert photo["json_body"]["chat_id"] == RECIPIENT_NICK
    assert "caption" not in photo["json_body"]
    assert photo["files"] == {"photo": ("bouquet.jpg", PHOTO_BYTES)}


def test_every_tray_photo_is_delivered_in_order(bot_env, api_calls):
    """Черновик с несколькими карточками: ни одно фото не теряется."""
    out = tg.send_outreach_to_client(
        text=CAPTION,
        tg_nick=RECIPIENT_NICK,
        images=[
            {"url": "https://market.example/a.jpg", "name": "a.jpg"},
            {"url": "//market.example/b.jpg", "name": "b.jpg"},
        ],
    )

    assert out["ok"] is True
    assert (out["photos_sent"], out["photos_total"]) == (2, 2)
    assert [c["method"] for c in api_calls] == ["sendMessage", "sendPhoto", "sendPhoto"]
    assert [c["json_body"]["photo"] for c in api_calls[1:]] == [
        "https://market.example/a.jpg",
        "https://market.example/b.jpg",
    ]


def test_send_data_url_photo_to_recipient(bot_env, api_calls):
    """Картинка из буфера/файла приходит в UI как data-URL."""
    out = tg.send_outreach_to_client(
        text=CAPTION,
        tg_nick=RECIPIENT_NICK,
        image_base64=f"data:image/jpeg;base64,{PHOTO_B64}",
    )

    assert out["ok"] is True
    assert api_calls[1]["files"] == {"photo": ("photo.jpg", PHOTO_BYTES)}


def test_send_card_photo_url_to_recipient(bot_env, api_calls):
    """Фото карточки маркетплейса — Telegram сам скачивает URL."""
    out = tg.send_outreach_to_client(
        text=CAPTION,
        tg_nick=RECIPIENT_NICK,
        image_url="https://market.example/veresk-101.jpg",
    )

    assert out["ok"] is True
    call = api_calls[1]
    assert call["method"] == "sendPhoto"
    assert call["json_body"]["photo"] == "https://market.example/veresk-101.jpg"
    assert call["files"] is None


def test_captioned_photo_still_splits_at_1024(bot_env, api_calls):
    """Уровень ниже: если подпись всё же используется, остаток не теряется."""
    long_text = CAPTION + "\n" + ("описание букета " * 200)
    assert len(long_text) > 1024

    out = tg.send_telegram_message(
        text=long_text,
        chat_id=RECIPIENT_NICK,
        image_base64=PHOTO_B64,
    )

    assert out["ok"] is True
    assert out["tail_ok"] is True
    assert [c["method"] for c in api_calls] == ["sendPhoto", "sendMessage"]
    assert api_calls[0]["json_body"]["caption"] == long_text[:1024]
    assert api_calls[1]["json_body"]["text"] == long_text[1024:].strip()
    assert api_calls[1]["json_body"]["chat_id"] == RECIPIENT_NICK


def test_long_draft_needs_no_caption_split(bot_env, api_calls):
    """Через тред целиком: длинный текст уходит целым сообщением, потом фото."""
    long_text = CAPTION + "\n" + ("описание букета " * 200)

    out = tg.send_outreach_to_client(
        text=long_text,
        tg_nick=RECIPIENT_NICK,
        image_base64=PHOTO_B64,
    )

    assert out["ok"] is True
    assert [c["method"] for c in api_calls] == ["sendMessage", "sendPhoto"]
    assert api_calls[0]["json_body"]["text"] == long_text.strip()
    assert "caption" not in api_calls[1]["json_body"]


def test_photo_send_without_token_reports_business_bot(monkeypatch):
    """Без токена ошибка должна называть Business-бота, а не молчать."""
    for key in (
        "TELEGRAM_BUSINESS_BOT_TOKEN",
        "MOYSKLAD_TELEGRAM_BOT_TOKEN",
        "TELEGRAM_BOT_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MOYSKLAD_TELEGRAM_SEND_VIA", "bot")

    out = tg.send_telegram_message(
        text=CAPTION,
        chat_id=RECIPIENT_NICK,
        image_base64=PHOTO_B64,
    )

    assert out["ok"] is False
    assert out["error"] == "telegram_token_missing"
    assert "Business" in out["detail"]


@pytest.mark.skipif(
    os.getenv("MOYSKLAD_TG_PHOTO_LIVE") != "1",
    reason="live send: set MOYSKLAD_TG_PHOTO_LIVE=1 plus a real bot token",
)
def test_live_photo_send_to_recipient():
    """Реальная отправка @pawels2137 — руками, не в CI.

    MOYSKLAD_TG_PHOTO_LIVE=1 MOYSKLAD_TG_PHOTO_URL=https://…jpg \
        pytest tests/plugins/test_moysklad_telegram_photo_send.py -k live
    """
    image_url = os.getenv("MOYSKLAD_TG_PHOTO_URL", "").strip()
    if not image_url:
        pytest.skip("set MOYSKLAD_TG_PHOTO_URL to the photo you want delivered")

    out = tg.send_outreach_to_client(
        text=CAPTION,
        tg_nick=RECIPIENT_NICK,
        image_url=image_url,
    )

    assert out["ok"] is True, out
    assert out["message_id"]


# --- Маршрутизация: фото не должно уходить ботом «в никуда» ----------------
#
# Регрессия, из-за которой ничего не приходило: фото всегда шло через
# Business-бота, а бот не может писать первым тому, кто ему не писал —
# Telegram отвечает chat not found / BUSINESS_PEER_INVALID. Личный аккаунт
# такие сообщения доставляет, поэтому он должен идти первым.


def test_photo_prefers_personal_account_over_bot(monkeypatch, bot_env, api_calls):
    sent: list[dict] = []

    def fake_send_photo(*, peer, caption, image_bytes, image_name, image_url):
        sent.append(
            {
                "peer": peer,
                "caption": caption,
                "has_bytes": bool(image_bytes),
                "image_name": image_name,
                "image_url": image_url,
            }
        )
        return {"ok": True, "message_id": 555, "chat_id": "796461007"}

    monkeypatch.setattr(tg.tg_user, "is_authorized", lambda **kw: True)
    monkeypatch.setattr(tg.tg_user, "send_photo", fake_send_photo)
    monkeypatch.setenv("MOYSKLAD_TELEGRAM_SEND_VIA", "auto")

    out = tg.send_outreach_to_client(
        text=CAPTION,
        tg_nick=RECIPIENT_NICK,
        image_base64=PHOTO_B64,
        image_name="bouquet.jpg",
    )

    assert out["ok"] is True
    assert out["photo_via"] == "user_account_photo"
    # Подпись пустая: текст ушёл своим сообщением.
    assert sent == [
        {
            "peer": RECIPIENT_NICK,
            "caption": "",
            "has_bytes": True,
            "image_name": "bouquet.jpg",
            "image_url": "",
        }
    ]
    # Фото бот не трогал — иначе получатель картинку бы не увидел.
    assert [c["method"] for c in api_calls] == ["sendMessage"]


def test_photo_falls_back_to_bot_when_personal_account_fails(
    monkeypatch, bot_env, api_calls
):
    monkeypatch.setattr(tg.tg_user, "is_authorized", lambda **kw: True)
    monkeypatch.setattr(
        tg.tg_user,
        "send_photo",
        lambda **kw: {"ok": False, "error": "not_authorized", "detail": "no session"},
    )
    monkeypatch.setenv("MOYSKLAD_TELEGRAM_SEND_VIA", "auto")

    out = tg.send_outreach_to_client(
        text=CAPTION,
        tg_nick=RECIPIENT_NICK,
        image_url="https://market.example/veresk-101.jpg",
    )

    assert out["ok"] is True
    assert out["photo_via"] == "business_bot_photo"
    assert [c["method"] for c in api_calls] == ["sendMessage", "sendPhoto"]


def test_photo_without_personal_account_still_uses_bot(monkeypatch, bot_env, api_calls):
    monkeypatch.setattr(tg.tg_user, "is_authorized", lambda **kw: False)
    monkeypatch.setenv("MOYSKLAD_TELEGRAM_SEND_VIA", "auto")

    out = tg.send_outreach_to_client(
        text=CAPTION,
        tg_nick=RECIPIENT_NICK,
        image_base64=PHOTO_B64,
    )

    assert out["ok"] is True
    assert out["photo_via"] == "business_bot_photo"


def test_photo_via_user_mode_falls_back_to_bot(monkeypatch, bot_env, api_calls):
    """via=user — при провале личного отправляем фото через бот, чтобы не терять вложение."""
    monkeypatch.setattr(tg.tg_user, "is_authorized", lambda **kw: True)
    monkeypatch.setattr(
        tg.tg_user,
        "send_photo",
        lambda **kw: {"ok": False, "error": "image_missing", "detail": "нет фото"},
    )

    out = tg.send_telegram_message(
        text=CAPTION,
        chat_id=RECIPIENT_NICK,
        image_base64=PHOTO_B64,
        via="user",
    )

    assert out["ok"] is True
    assert out["via"] == "business_bot_photo"
    assert [c["method"] for c in api_calls] == ["sendPhoto"]


def test_personal_photo_splits_long_caption(monkeypatch, bot_env, api_calls):
    long_text = CAPTION + "\n" + ("описание букета " * 200)
    assert len(long_text) > 1024
    tails: list[str] = []

    monkeypatch.setattr(tg.tg_user, "is_authorized", lambda **kw: True)
    monkeypatch.setattr(
        tg.tg_user,
        "send_photo",
        lambda **kw: {"ok": True, "message_id": 1, "chat_id": "796461007"},
    )
    monkeypatch.setattr(
        tg.tg_user,
        "send_message",
        lambda *, peer, text: tails.append(text) or {"ok": True, "message_id": 2},
    )
    monkeypatch.setattr(tg.tg_user, "load_config", lambda: {})
    monkeypatch.setenv("MOYSKLAD_TELEGRAM_SEND_VIA", "auto")

    out = tg.send_telegram_message(
        text=long_text,
        chat_id=RECIPIENT_NICK,
        image_base64=PHOTO_B64,
    )

    assert out["ok"] is True
    assert out["tail_ok"] is True
    # Подпись обрезана на 1024 — в хвост уходит ровно остаток (текст стрипается).
    assert tails == [long_text.strip()[1024:]]
