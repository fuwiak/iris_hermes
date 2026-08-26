"""sendPhoto delivery: base64 upload vs remote URL, caption handling."""

from __future__ import annotations

import base64

import plugins.moysklad.telegram_send as ts


def _stub_env(monkeypatch, calls):
    monkeypatch.setattr(ts, "outreach_bot_token", lambda: "bot-token")
    monkeypatch.setattr(ts, "resolve_business_connection_id", lambda: "")
    # These cases cover the Business-bot path. Photos now try the personal
    # account first, so pin the mode — otherwise a dev machine with a live
    # MTProto session would dial Telegram for real mid-test.
    monkeypatch.setenv("MOYSKLAD_TELEGRAM_SEND_VIA", "bot")
    monkeypatch.setattr(ts.tg_user, "is_authorized", lambda **kw: False)

    def fake_api(method, *, token=None, params=None, json_body=None, files=None, timeout=30.0):
        calls.append({"method": method, "json_body": json_body, "files": files})
        return {"ok": True, "result": {"message_id": 7, "chat": {"id": 123}}}

    monkeypatch.setattr(ts, "telegram_api", fake_api)


def test_photo_by_url_goes_as_json(monkeypatch):
    calls: list = []
    _stub_env(monkeypatch, calls)
    out = ts.send_telegram_message(
        text="Букет",
        chat_id="123",
        image_url="https://content2.flowwow-images.com/x.jpg",
    )
    assert out["ok"] is True and out["via"] == "business_bot_photo"
    call = calls[0]
    assert call["method"] == "sendPhoto"
    assert call["files"] is None
    assert call["json_body"]["photo"] == "https://content2.flowwow-images.com/x.jpg"
    assert call["json_body"]["caption"] == "Букет"


def test_photo_by_base64_goes_multipart(monkeypatch):
    calls: list = []
    _stub_env(monkeypatch, calls)
    payload = base64.b64encode(b"fakejpg").decode()
    out = ts.send_telegram_message(
        text="x", chat_id="123", image_base64=payload, image_name="a.jpg"
    )
    assert out["ok"] is True
    call = calls[0]
    assert call["method"] == "sendPhoto"
    assert call["files"] == {"photo": ("a.jpg", b"fakejpg")}
    assert "photo" not in (call["json_body"] or {})


def test_long_caption_splits_into_tail_message(monkeypatch):
    calls: list = []
    _stub_env(monkeypatch, calls)
    long_text = "х" * 1500
    out = ts.send_telegram_message(text=long_text, chat_id="123", image_url="https://a/b.jpg")
    assert out["ok"] is True and out.get("tail_ok") is True
    assert calls[0]["method"] == "sendPhoto"
    assert len(calls[0]["json_body"]["caption"]) == 1024
    assert calls[1]["method"] == "sendMessage"
    assert calls[1]["json_body"]["text"] == long_text[1024:]
