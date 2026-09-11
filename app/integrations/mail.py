"""Отправка почты.

Без настроенного SMTP письма уходят в лог, а не теряются молча: локальная
разработка и тесты не должны требовать почтового сервера. Для просмотра писем
целиком в docker-compose поднят Mailpit (http://127.0.0.1:8026).
"""

import logging
from collections import deque
from dataclasses import dataclass, field
from email.message import EmailMessage

import aiosmtplib

from ..config import Settings

logger = logging.getLogger(__name__)

# Заглушка живёт столько же, сколько процесс: без предела письма (вместе с
# кодами в теле) копились бы в памяти до перезапуска.
OUTBOX_LIMIT = 200


@dataclass(slots=True)
class SentMessage:
    to: str
    subject: str
    body: str


class Mailer:
    async def send(self, to: str, subject: str, body: str) -> None:  # pragma: no cover
        raise NotImplementedError


@dataclass
class LogMailer(Mailer):
    """Заглушка: письмо пишется в лог и накапливается для тестов."""

    outbox: deque[SentMessage] = field(default_factory=lambda: deque(maxlen=OUTBOX_LIMIT))

    async def send(self, to: str, subject: str, body: str) -> None:
        self.outbox.append(SentMessage(to=to, subject=subject, body=body))
        # Ни темы, ни тела: письмо с кодом подтверждения не должно оседать в
        # журнале даже в виде заголовка.
        logger.info("Письмо не отправлено (SMTP не настроен), получатель: %s", to)


@dataclass
class SmtpMailer(Mailer):
    settings: Settings

    async def send(self, to: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self.settings.mail_from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)

        await aiosmtplib.send(
            message,
            hostname=self.settings.smtp_host,
            port=self.settings.smtp_port,
            username=self.settings.smtp_username or None,
            password=self.settings.smtp_password or None,
            use_tls=self.settings.smtp_use_tls,
            start_tls=self.settings.smtp_start_tls or None,
            timeout=self.settings.mail_timeout_seconds,
        )


def create_mailer(settings: Settings) -> Mailer:
    return SmtpMailer(settings) if settings.smtp_host else LogMailer()
