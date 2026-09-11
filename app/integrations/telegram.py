"""Уведомления в Telegram.

Без токена бота сообщения пишутся в лог: отсутствие интеграции не должно
ронять отправку остальных каналов.
"""

import logging
from collections import deque
from dataclasses import dataclass, field

import httpx

from ..config import Settings

logger = logging.getLogger(__name__)

# Как и у почтовой заглушки: без предела сообщения копятся в памяти процесса.
OUTBOX_LIMIT = 200


@dataclass(slots=True)
class SentTelegramMessage:
    chat_id: str
    text: str


class TelegramSender:
    async def send(self, chat_id: str, text: str) -> bool:  # pragma: no cover
        raise NotImplementedError


@dataclass
class LogTelegramSender(TelegramSender):
    outbox: deque[SentTelegramMessage] = field(default_factory=lambda: deque(maxlen=OUTBOX_LIMIT))

    async def send(self, chat_id: str, text: str) -> bool:
        self.outbox.append(SentTelegramMessage(chat_id=chat_id, text=text))
        logger.info("Telegram не настроен, сообщение в лог: chat=%s", chat_id)
        return True


@dataclass
class BotTelegramSender(TelegramSender):
    settings: Settings

    def _redact(self, text: str) -> str:
        """Токен не должен пройти в лог даже если сервер вернул его в ответе."""
        token = self.settings.telegram_bot_token
        return text.replace(token, "***") if token else text

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
                logger.warning(
                    "Telegram отклонил сообщение: HTTP %s, %s",
                    response.status_code,
                    self._redact(response.text[:200]),
                )
                return False
            return True
        except httpx.HTTPError as error:
            # Строка исключения httpx нередко содержит URL запроса, а в нём —
            # токен бота. В журнал уходит только класс ошибки.
            logger.warning("Telegram недоступен: %s", type(error).__name__)
            return False


def create_telegram_sender(settings: Settings) -> TelegramSender:
    return BotTelegramSender(settings) if settings.telegram_bot_token else LogTelegramSender()
