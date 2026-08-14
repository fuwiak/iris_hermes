"""История отправок: flat outbound feed from the conversations store."""

from __future__ import annotations

from plugins.moysklad.conversations import append_message, clear_memory_for_tests
from plugins.moysklad.sent_history import list_sent_messages


def test_sent_feed_includes_single_sends_with_status(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    clear_memory_for_tests()

    append_message(
        client_id="c-1",
        text="Одиночное с карточки",
        direction="outbound",
        tg_nick="@maria",
        client_name="Мария",
        source="client_card_send",
    )
    append_message(
        client_id="c-2",
        text="Доставлено ботом",
        direction="outbound",
        client_name="Пётр",
        source="campaign_telegram_bot",
    )
    append_message(
        client_id="c-2",
        text="Ответ клиента",
        direction="inbound",
        source="telegram_user",
    )

    rows = list_sent_messages(limit=50)
    assert len(rows) == 2  # inbound excluded
    by_client = {r["client_id"]: r for r in rows}
    assert by_client["c-1"]["status"] == "recorded"
    assert by_client["c-2"]["status"] == "delivered"
    assert by_client["c-1"]["text"] == "Одиночное с карточки"
    # newest first
    assert rows[0]["ts"] >= rows[1]["ts"]
