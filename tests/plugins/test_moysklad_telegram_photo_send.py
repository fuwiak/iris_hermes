"""Отправка сообщения с фото конкретному получателю (@pawels2137).

Проверяет весь путь «ник → chat_id → sendPhoto»: именно он ломался тише
всего, потому что текстовая отправка продолжала работать, а фото молча
уходило в другой метод Bot API.

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
    out = tg.send_outreach_to_client(
        text=CAPTION,
        tg_nick=RECIPIENT_NICK,
        image_base64=PHOTO_B64,
        image_name="bouquet.jpg",
    )

    assert out["ok"] is True
    assert out["via"] == "business_bot_photo"
    assert out["message_id"] == 4242

    assert len(api_calls) == 1
    call = api_calls[0]
    assert call["method"] == "sendPhoto"
    assert call["json_body"]["chat_id"] == RECIPIENT_NICK
    assert call["json_body"]["caption"] == CAPTION
    assert call["files"] == {"photo": ("bouquet.jpg", PHOTO_BYTES)}


def test_send_data_url_photo_to_recipient(bot_env, api_calls):
    """Картинка из буфера/файла приходит в UI как data-URL."""
    out = tg.send_outreach_to_client(
        text=CAPTION,
        tg_nick=RECIPIENT_NICK,
        image_base64=f"data:image/jpeg;base64,{PHOTO_B64}",
    )

    assert out["ok"] is True
    assert api_calls[0]["files"] == {"photo": ("photo.jpg", PHOTO_BYTES)}


def test_send_card_photo_url_to_recipient(bot_env, api_calls):
    """Фото карточки маркетплейса — Telegram сам скачивает URL."""
    out = tg.send_outreach_to_client(
        text=CAPTION,
        tg_nick=RECIPIENT_NICK,
        image_url="https://market.example/veresk-101.jpg",
    )

    assert out["ok"] is True
    call = api_calls[0]
    assert call["method"] == "sendPhoto"
    assert call["json_body"]["photo"] == "https://market.example/veresk-101.jpg"
    assert call["files"] is None


def test_long_text_rides_as_caption_plus_tail(bot_env, api_calls):
    """Caption обрезан на 1024 — остаток должен дойти отдельным сообщением."""
    long_text = CAPTION + "\n" + ("описание букета " * 200)
    assert len(long_text) > 1024

    out = tg.send_outreach_to_client(
        text=long_text,
        tg_nick=RECIPIENT_NICK,
        image_base64=PHOTO_B64,
    )

    assert out["ok"] is True
    assert out["tail_ok"] is True
    methods = [c["method"] for c in api_calls]
    assert methods == ["sendPhoto", "sendMessage"]
    assert api_calls[0]["json_body"]["caption"] == long_text[:1024]
    # Хвост уходит отдельным сообщением; send_telegram_message его стрипает.
    assert api_calls[1]["json_body"]["text"] == long_text[1024:].strip()
    assert api_calls[1]["json_body"]["chat_id"] == RECIPIENT_NICK


def test_photo_send_without_token_reports_business_bot(monkeypatch):
    """Без токена ошибка должна называть Business-бота, а не молчать."""
    for key in (
        "TELEGRAM_BUSINESS_BOT_TOKEN",
        "MOYSKLAD_TELEGRAM_BOT_TOKEN",
        "TELEGRAM_BOT_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MOYSKLAD_TELEGRAM_SEND_VIA", "bot")

    out = tg.send_outreach_to_client(
        text=CAPTION,
        tg_nick=RECIPIENT_NICK,
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
