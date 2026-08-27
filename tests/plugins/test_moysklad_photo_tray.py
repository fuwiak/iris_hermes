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

    def test_user_mode_still_owns_uploaded_bytes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An uploaded photo is exactly what `user` mode is for — no bot detour."""
        monkeypatch.setenv("TELEGRAM_BUSINESS_BOT_TOKEN", "1:TESTTOKEN")
        monkeypatch.setattr(ts.tg_user, "is_authorized", lambda **kw: True)
        monkeypatch.setattr(
            ts.tg_user,
            "send_photo",
            lambda **kw: {"ok": False, "error": "not_authorized", "detail": "no session"},
        )
        calls: list[str] = []
        monkeypatch.setattr(
            ts, "telegram_api", lambda method, **kw: calls.append(method) or {"ok": True}
        )

        out = ts.send_telegram_message(
            text="",
            chat_id="@someone",
            image_base64="data:image/jpeg;base64,/9j/4AAQ",
            via="user",
        )

        assert out["ok"] is False
        assert out["error"] == "not_authorized"
        assert calls == []

    def test_undownloadable_photo_names_the_reason(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No raw errno in the seller's face — say what to do instead."""

        def boom(*a: Any, **k: Any) -> None:
            raise OSError("[Errno 101] Network is unreachable")

        monkeypatch.setattr(ts, "_download_image_url", boom, raising=True)
        monkeypatch.setattr(ts, "_LAST_IMAGE_FETCH_ERROR", "", raising=False)
        # A relative URL is unusable regardless of the network — same branch.
        ts._LAST_IMAGE_FETCH_ERROR = "[Errno 101] Network is unreachable"

        out = ts.send_telegram_message(text="привет", chat_id="42", image_url="cards/a.jpg")

        assert out["ok"] is False
        assert out["error"] == "image_unusable"
        assert "Приложите файл вручную" in out["detail"]
