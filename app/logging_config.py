"""Единая настройка логирования для API и воркера.

httpx пишет каждый запрос на уровне INFO вместе с полным URL. У Telegram bot
token лежит прямо в пути (`/bot{token}/sendMessage`), поэтому при INFO токен
оказывается в журнале независимо от аккуратности собственных логгеров. Оба
процесса поднимают логирование только отсюда, чтобы правило не пришлось
повторять в каждой точке входа.
"""

import logging

# Клиентские логгеры HTTP: их INFO — это URL запросов и заголовки соединений.
# anthropic и aiosmtplib пишут тела запросов и SMTP-диалог на DEBUG.
QUIET_LOGGERS = ("httpx", "httpcore", "anthropic", "aiosmtplib")


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
