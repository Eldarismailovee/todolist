"""Уведомления в Telegram.

Без токена бота сообщения пишутся в лог: отсутствие интеграции не должно
ронять отправку остальных каналов.
"""

import logging
from dataclasses import dataclass, field

import httpx

from ..config import Settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SentTelegramMessage:
    chat_id: str
    text: str


class TelegramSender:
    async def send(self, chat_id: str, text: str) -> bool:  # pragma: no cover
        raise NotImplementedError


@dataclass
class LogTelegramSender(TelegramSender):
    outbox: list[SentTelegramMessage] = field(default_factory=list)

    async def send(self, chat_id: str, text: str) -> bool:
        self.outbox.append(SentTelegramMessage(chat_id=chat_id, text=text))
        logger.info("Telegram не настроен, сообщение в лог: chat=%s", chat_id)
        return True


@dataclass
class BotTelegramSender(TelegramSender):
    settings: Settings

    async def send(self, chat_id: str, text: str) -> bool:
        url = f"{self.settings.telegram_api_base}/bot{self.settings.telegram_bot_token}/sendMessage"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    url,
                    json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                )
            if response.status_code >= 400:
                # Текст ошибки Telegram полезен (например, «chat not found»),
                # но токен в URL в лог попадать не должен.
                logger.warning("Telegram отклонил сообщение: %s", response.text[:200])
                return False
            return True
        except httpx.HTTPError as error:
            logger.warning("Telegram недоступен: %s", error)
            return False


def create_telegram_sender(settings: Settings) -> TelegramSender:
    return BotTelegramSender(settings) if settings.telegram_bot_token else LogTelegramSender()
