"""Photo tray → Telegram: every attached picture is appended after the text."""

from __future__ import annotations

from typing import Any

import pytest

from plugins.moysklad import telegram_send as ts


@pytest.fixture()
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record every send_telegram_message call instead of hitting Telegram."""
    calls: list[dict[str, Any]] = []

    def fake(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"ok": True, "message_id": len(calls), "chat_id": kwargs.get("chat_id")}

    monkeypatch.setattr(ts, "send_telegram_message", fake)
    return calls


class TestNormalizeImageAttachments:
    def test_upgrades_protocol_relative_and_keeps_order(self) -> None:
        out = ts.normalize_image_attachments(
            [{"url": "//cdn/a.jpg", "name": "A"}, {"url": "https://cdn/b.jpg", "name": "B"}]
        )
        assert [item["image_url"] for item in out] == [
            "https://cdn/a.jpg",
            "https://cdn/b.jpg",
        ]
        assert [item["image_name"] for item in out] == ["A", "B"]

    def test_data_url_never_lands_in_image_url(self) -> None:
        data = "data:image/png;base64,aa"
        assert ts.normalize_image_attachments([{"url": data, "name": "p.jpg"}]) == [
            {"image_url": "", "image_base64": data, "image_name": "p.jpg"}
        ]

    def test_legacy_single_photo_fields_join_the_tray(self) -> None:
        out = ts.normalize_image_attachments(
            [{"url": "https://cdn/a.jpg"}], "", "photo.jpg", "https://cdn/b.jpg"
        )
        assert [item["image_url"] for item in out] == [
            "https://cdn/a.jpg",
            "https://cdn/b.jpg",
        ]

    def test_same_picture_is_never_sent_twice(self) -> None:
        out = ts.normalize_image_attachments(
            [{"url": "https://cdn/a.jpg"}], "", "photo.jpg", "https://cdn/a.jpg"
        )
        assert len(out) == 1

    def test_caps_at_ten(self) -> None:
        many = [{"url": f"https://cdn/{i}.jpg"} for i in range(25)]
        assert len(ts.normalize_image_attachments(many)) == ts.MAX_OUTREACH_PHOTOS

    def test_empty_tray_is_empty(self) -> None:
        assert ts.normalize_image_attachments([], "", "photo.jpg", "") == []


class TestSendTelegramBundle:
    def test_no_photos_is_a_plain_text_send(self, sent: list[dict[str, Any]]) -> None:
        out = ts.send_telegram_bundle(text="привет", chat_id="42", attachments=[])
        assert out["ok"] is True
        assert len(sent) == 1
        assert sent[0]["text"] == "привет"
        assert not sent[0].get("image_url")

    def test_text_goes_first_then_every_photo(self, sent: list[dict[str, Any]]) -> None:
        out = ts.send_telegram_bundle(
            text="привет",
            chat_id="42",
            attachments=ts.normalize_image_attachments(
                [{"url": "https://cdn/a.jpg"}, {"url": "https://cdn/b.jpg"}]
            ),
        )
        assert out["ok"] is True
        assert out["photos_sent"] == 2
        assert out["photos_total"] == 2
        assert [call["text"] for call in sent] == ["привет", "", ""]
        assert [call.get("image_url") for call in sent[1:]] == [
            "https://cdn/a.jpg",
            "https://cdn/b.jpg",
        ]

    def test_photos_still_go_when_the_draft_has_no_text(
        self, sent: list[dict[str, Any]]
    ) -> None:
        out = ts.send_telegram_bundle(
            text="   ",
            chat_id="42",
            attachments=ts.normalize_image_attachments([{"url": "https://cdn/a.jpg"}]),
        )
        assert out["ok"] is True
        assert len(sent) == 1
        assert sent[0]["image_url"] == "https://cdn/a.jpg"

    def test_a_failed_photo_is_never_reported_as_a_clean_send(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The original bug: text delivered, photo dropped, UI said ✓."""
        calls: list[dict[str, Any]] = []

        def fake(**kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            if kwargs.get("image_url"):
                return {"ok": False, "error": "image_unusable", "detail": "битая ссылка"}
            return {"ok": True, "message_id": 1, "chat_id": "42"}

        monkeypatch.setattr(ts, "send_telegram_message", fake)
        out = ts.send_telegram_bundle(
            text="привет",
            chat_id="42",
            attachments=ts.normalize_image_attachments([{"url": "https://cdn/a.jpg"}]),
        )
        assert out["ok"] is False
        assert out["photos_sent"] == 0
        assert "битая ссылка" in out["detail"]

    def test_a_failed_text_send_stops_before_the_photos(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[dict[str, Any]] = []

        def fake(**kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {"ok": False, "error": "telegram_chat_missing"}

        monkeypatch.setattr(ts, "send_telegram_message", fake)
        out = ts.send_telegram_bundle(
            text="привет",
            chat_id="",
            attachments=ts.normalize_image_attachments([{"url": "https://cdn/a.jpg"}]),
        )
        assert out["ok"] is False
        assert len(calls) == 1


class TestUnreachableCdn:
    """Backend without egress to the marketplace CDN must not eat the photo."""

    def test_url_only_photo_falls_back_to_the_bot_even_in_user_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """MTProto down + URL photo: the bot can still hand the URL to Telegram.

        Regression: «Текст ушёл, фото 0/1: [Errno 101] Network is unreachable».
        """
        monkeypatch.setenv("TELEGRAM_BUSINESS_BOT_TOKEN", "1:TESTTOKEN")
        monkeypatch.setattr(ts, "_download_image_url", lambda *a, **k: None)
        monkeypatch.setattr(ts.tg_user, "is_authorized", lambda **kw: True)
        monkeypatch.setattr(
            ts.tg_user,
            "send_photo",
            lambda **kw: (_ for _ in ()).throw(OSError("[Errno 101] Network is unreachable")),
        )

        calls: list[str] = []

        def fake_api(method: str, **kw: Any) -> dict[str, Any]:
            calls.append(method)
            return {"ok": True, "result": {"message_id": 7, "chat": {"id": 1}}}

        monkeypatch.setattr(ts, "telegram_api", fake_api)

        out = ts.send_telegram_message(
            text="",
            chat_id="@someone",
            image_url="https://cdn.example/a.jpg",
            via="user",
        )

        assert out["ok"] is True
        assert calls == ["sendPhoto"]

    def test_user_mode_uploaded_bytes_still_fall_back_to_bot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Personal leg dead → bot must still try; otherwise text-ok / photo-gone."""
        monkeypatch.setenv("TELEGRAM_BUSINESS_BOT_TOKEN", "1:TESTTOKEN")
        monkeypatch.setattr(ts.tg_user, "is_authorized", lambda **kw: True)
        monkeypatch.setattr(
            ts.tg_user,
            "send_photo",
            lambda **kw: {
                "ok": False,
                "error": "gateway_no_photo",
                "detail": "gateway text-only",
            },
        )
        calls: list[str] = []

        def fake_api(method: str, **kw: Any) -> dict[str, Any]:
            calls.append(method)
            return {"ok": True, "result": {"message_id": 9, "chat": {"id": 1}}}

        monkeypatch.setattr(ts, "telegram_api", fake_api)
        monkeypatch.setattr(ts, "resolve_business_connection_id", lambda: "")

        out = ts.send_telegram_message(
            text="",
            chat_id="42",
            image_base64="data:image/jpeg;base64,/9j/4AAQ",
            via="user",
        )

        assert out["ok"] is True
        assert calls == ["sendPhoto"]


class TestOutreachWireTextThenPhoto:
    """Prove the real Bot API sequence: sendMessage, then sendPhoto — not caption."""

    def test_send_outreach_fires_sendMessage_then_sendPhoto(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TELEGRAM_BUSINESS_BOT_TOKEN", "1:TESTTOKEN")
        monkeypatch.setenv("MOYSKLAD_TELEGRAM_SEND_VIA", "bot")
        monkeypatch.setattr(ts, "resolve_business_connection_id", lambda: "")
        monkeypatch.setattr(ts.tg_user, "is_authorized", lambda **kw: False)
        monkeypatch.setattr(ts, "_download_image_url", lambda *a, **k: None)

        wire: list[dict[str, Any]] = []

        def fake_api(method: str, **kwargs: Any) -> dict[str, Any]:
            wire.append(
                {
                    "method": method,
                    "json": dict(kwargs.get("json_body") or {}),
                    "has_files": bool(kwargs.get("files")),
                }
            )
            return {
                "ok": True,
                "result": {"message_id": len(wire), "chat": {"id": 4242}},
            }

        monkeypatch.setattr(ts, "telegram_api", fake_api)

        out = ts.send_outreach_to_client(
            text="привет, вот букет",
            tg_chat_id="123456789",
            via="bot",
            images=[
                {
                    "image_url": "https://cdn.example/bouquet.jpg",
                    "image_base64": "",
                    "image_name": "Букет",
                }
            ],
        )

        assert out["ok"] is True, out
        assert out.get("photos_sent") == 1
        assert out.get("photos_total") == 1
        assert [row["method"] for row in wire] == ["sendMessage", "sendPhoto"]
        assert wire[0]["json"].get("text") == "привет, вот букет"
        assert "photo" not in wire[0]["json"]
        # Follow-up photo: empty caption, URL handed to Bot API.
        assert wire[1]["json"].get("photo") == "https://cdn.example/bouquet.jpg"
        assert not wire[1]["json"].get("caption")

    def test_send_outreach_with_uploaded_bytes_uses_multipart_sendPhoto(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TELEGRAM_BUSINESS_BOT_TOKEN", "1:TESTTOKEN")
        monkeypatch.setattr(ts, "resolve_business_connection_id", lambda: "")
        monkeypatch.setattr(ts.tg_user, "is_authorized", lambda **kw: False)

        wire: list[dict[str, Any]] = []

        def fake_api(method: str, **kwargs: Any) -> dict[str, Any]:
            wire.append(
                {
                    "method": method,
                    "json": dict(kwargs.get("json_body") or {}),
                    "has_files": bool(kwargs.get("files")),
                }
            )
            return {
                "ok": True,
                "result": {"message_id": len(wire), "chat": {"id": 123456789}},
            }

        monkeypatch.setattr(ts, "telegram_api", fake_api)

        # Valid minimal JPEG bytes as data-URL (bad padding used to decode→None
        # and silently skip sendPhoto — that was part of the «фото не ушло» lie).
        import base64

        tiny = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xd9").decode("ascii")

        out = ts.send_outreach_to_client(
            text="текст сначала",
            tg_chat_id="123456789",
            via="bot",
            images=[{"image_url": "", "image_base64": tiny, "image_name": "p.jpg"}],
        )

        assert out["ok"] is True, out
        assert [row["method"] for row in wire] == ["sendMessage", "sendPhoto"]
        assert wire[0]["json"].get("text") == "текст сначала"
        assert wire[1]["has_files"] is True
        assert not wire[1]["json"].get("caption")

    def test_pydantic_tray_models_do_not_drop_photos(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """mark-sent passes ImageAttachment models — must not normalize to []."""
        from plugins.moysklad.dashboard.plugin_api import ImageAttachment

        monkeypatch.setenv("TELEGRAM_BUSINESS_BOT_TOKEN", "1:TESTTOKEN")
        monkeypatch.setattr(ts, "resolve_business_connection_id", lambda: "")
        monkeypatch.setattr(ts.tg_user, "is_authorized", lambda **kw: False)
        monkeypatch.setattr(ts, "_download_image_url", lambda *a, **k: None)

        wire: list[str] = []
        monkeypatch.setattr(
            ts,
            "telegram_api",
            lambda method, **kw: wire.append(method)
            or {"ok": True, "result": {"message_id": len(wire), "chat": {"id": 123456789}}},
        )

        tray = [
            ImageAttachment(
                image_url="https://cdn.example/a.jpg",
                image_base64="",
                image_name="A",
            )
        ]
        out = ts.send_outreach_to_client(
            text="hi",
            tg_chat_id="123456789",
            via="bot",
            images=tray,
        )
        assert out["ok"] is True
        assert wire == ["sendMessage", "sendPhoto"]


class TestGatewayPhotoFallback:
    def test_gateway_send_photo_ok_is_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from plugins.platforms.telegram_user import client as tu

        monkeypatch.setenv("TELEGRAM_USER_GATEWAY_URL", "https://gw.example/t/x")
        monkeypatch.setattr(
            tu,
            "_gateway_request",
            lambda method, path, **kw: {
                "ok": True,
                "message_id": 55,
                "chat_id": "9",
                "via": "gateway",
            }
            if path == "send_photo"
            else {"ok": False, "error": "unexpected"},
        )
        # Local path must not run when gateway succeeds.
        monkeypatch.setattr(
            tu,
            "_call",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("local must not run")),
        )

        out = tu.send_photo(
            peer="9",
            caption="",
            image_bytes=b"\xff\xd8\xff",
            image_name="x.jpg",
        )
        assert out["ok"] is True
        assert out["via"] == "user_account_photo_gateway"

    def test_text_only_gateway_falls_through_to_local(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from plugins.platforms.telegram_user import client as tu

        monkeypatch.setenv("TELEGRAM_USER_GATEWAY_URL", "https://gw.example/t/x")
        monkeypatch.setattr(
            tu,
            "_gateway_request",
            lambda method, path, **kw: {
                "ok": False,
                "error": "not_found",
                "detail": "no send_photo",
            },
        )
        monkeypatch.setattr(
            tu,
            "_call",
            lambda fn, timeout=120.0: {
                "ok": True,
                "message_id": 1,
                "chat_id": "9",
                "via": "user_account_photo",
            },
        )

        out = tu.send_photo(
            peer="@nick",
            image_url="https://cdn/a.jpg",
        )
        assert out["ok"] is True
        assert out["via"] == "user_account_photo"


def test_undownloadable_photo_names_the_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """No raw errno in the seller's face — say what to do instead."""
    monkeypatch.setattr(ts, "_LAST_IMAGE_FETCH_ERROR", "", raising=False)
    ts._LAST_IMAGE_FETCH_ERROR = "[Errno 101] Network is unreachable"

    out = ts.send_telegram_message(text="привет", chat_id="42", image_url="cards/a.jpg")

    assert out["ok"] is False
    assert out["error"] == "image_unusable"
    assert "Приложите файл вручную" in out["detail"]
