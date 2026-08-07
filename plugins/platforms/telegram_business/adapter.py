"""Telegram Business platform adapter (Office / Офис).

Validates Business bot credentials on connect. Does NOT poll getUpdates —
the regular Telegram gateway adapter (or Railway webhook) owns inbound.
Outbound CRM sends use ``client.py`` / MoySklad ``telegram_send``.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, SendResult

from .client import (
    business_bot_token,
    probe_business_integration,
    resolve_business_connection_id,
)

logger = logging.getLogger(__name__)


def check_requirements() -> bool:
    return True


def validate_config(config: PlatformConfig) -> bool:
    token = (config.token or business_bot_token() or "").strip()
    biz = (
        str((config.extra or {}).get("business_connection_id") or "").strip()
        or resolve_business_connection_id(include_seller_settings=False)
    )
    return bool(token and biz)


def is_connected(config: PlatformConfig) -> bool:
    return validate_config(config)


def _env_enablement() -> dict[str, Any] | None:
    token = business_bot_token()
    biz = resolve_business_connection_id(include_seller_settings=False)
    if not token or not biz:
        return None
    return {
        "business_connection_id": biz,
        "bot_username": (
            (os.getenv("TELEGRAM_BUSINESS_BOT_USERNAME") or "").strip().lstrip("@")
            or None
        ),
    }


class TelegramBusinessAdapter(BasePlatformAdapter):
    """Credential probe + optional outbound send; no inbound loop."""

    supports_async_delivery = False
    interactive_resume = False

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform("telegram_business"))
        self._account_username: Optional[str] = None

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        token = (self.config.token or business_bot_token() or "").strip()
        if not token:
            self._fatal_error_code = "missing_token"
            self._fatal_error_message = "TELEGRAM_BUSINESS_BOT_TOKEN not set"
            self._fatal_error_retryable = False
            return False
        biz = (
            str((self.config.extra or {}).get("business_connection_id") or "").strip()
            or resolve_business_connection_id()
        )
        if not biz:
            self._fatal_error_code = "missing_connection"
            self._fatal_error_message = "TELEGRAM_BUSINESS_CONNECTION_ID not set"
            self._fatal_error_retryable = False
            return False

        result = probe_business_integration(
            token=token, business_connection_id=biz
        )
        if not result.get("ok"):
            self._fatal_error_code = "probe_failed"
            self._fatal_error_message = str(result.get("message") or "probe failed")
            self._fatal_error_retryable = True
            logger.warning("telegram_business connect failed: %s", self._fatal_error_message)
            return False

        account = (result.get("telegram_account") or {}).get("account") or {}
        self._account_username = account.get("username")
        self._running = True
        logger.info(
            "telegram_business connected: bot→@%s",
            self._account_username or "?",
        )
        return True

    async def disconnect(self) -> None:
        self._running = False

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        # Prefer MoySklad send path (coercion + business_connection_id).
        try:
            from plugins.moysklad.telegram_send import send_telegram_message
        except Exception as exc:
            return SendResult(success=False, error=str(exc))

        out = send_telegram_message(text=content, chat_id=str(chat_id))
        if out.get("ok"):
            return SendResult(
                success=True,
                message_id=str(out.get("message_id") or ""),
            )
        return SendResult(
            success=False,
            error=str(out.get("detail") or out.get("error") or "send failed"),
        )

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        return {
            "name": self._account_username or "Telegram Business",
            "type": "dm",
            "id": chat_id,
        }


def register(ctx) -> None:
    ctx.register_platform(
        name="telegram_business",
        label="Telegram Business",
        adapter_factory=lambda cfg: TelegramBusinessAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        is_connected=is_connected,
        required_env=[
            "TELEGRAM_BUSINESS_BOT_TOKEN",
            "TELEGRAM_BUSINESS_CONNECTION_ID",
        ],
        install_hint=(
            "Telegram → Settings → Business → Chatbots → link a bot, enable Reply. "
            "Paste bot token + business_connection_id in Office."
        ),
        env_enablement_fn=_env_enablement,
        emoji="💼",
        pii_safe=False,
        allow_update_command=False,
        platform_hint=(
            "Telegram Business is an outbound CRM channel (send on behalf of a "
            "linked shop account). It is not the main Telegram chat adapter."
        ),
    )
