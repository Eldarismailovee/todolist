"""Одноразовые коды подтверждения входа и регистрации.

Код короткий (6 цифр), поэтому в базе лежит HMAC с серверным секретом, а не
обычный хеш: иначе утёкшую таблицу перебрали бы за секунды. Число попыток
ограничено, код одноразовый и живёт минуты.
"""

import hmac
import logging
import secrets
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .models import OtpCode

logger = logging.getLogger(__name__)

# delete_account — подтверждение чувствительного действия, а не входа: код
# этой цели не принимается на /auth/otp/verify и сессии не создаёт.
Purpose = Literal["login", "register", "delete_account"]

ACTIONS: dict[str, str] = {
    "login": "входа",
    "register": "регистрации",
    "delete_account": "удаления аккаунта",
}


def generate_code(settings: Settings) -> str:
    """Криптографически стойкий код фиксированной длины, включая ведущие нули."""
    upper = 10**settings.otp_length
    return str(secrets.randbelow(upper)).zfill(settings.otp_length)


def hash_code(settings: Settings, email: str, code: str) -> str:
    message = f"{email.lower()}:{code}".encode()
    return hmac.new(settings.secret_key.get_secret_value().encode(), message, sha256).hexdigest()


async def issue_code(
    db: AsyncSession,
    settings: Settings,
    email: str,
    purpose: Purpose,
    *,
    pending_password_hash: str | None = None,
) -> str:
    """Создаёт код, гася все предыдущие для этой пары email/цель."""
    email = email.lower()
    now = datetime.now(UTC)
    await db.execute(
        update(OtpCode)
        .where(
            OtpCode.email == email,
            OtpCode.purpose == purpose,
            OtpCode.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )

    code = generate_code(settings)
    db.add(
        OtpCode(
            email=email,
            code_hash=hash_code(settings, email, code),
            purpose=purpose,
            pending_password_hash=pending_password_hash,
            expires_at=now + timedelta(seconds=settings.otp_ttl_seconds),
        )
    )
    await db.flush()
    return code


class OtpError(Exception):
    """Код не подошёл: истёк, исчерпал попытки или не совпал."""


async def consume_code(
    db: AsyncSession, settings: Settings, email: str, code: str, purpose: Purpose
) -> OtpCode:
    """Проверяет код и помечает его использованным. Ошибки неразличимы снаружи."""
    email = email.lower()
    now = datetime.now(UTC)

    record = await db.scalar(
        select(OtpCode)
        .where(
            OtpCode.email == email,
            OtpCode.purpose == purpose,
            OtpCode.consumed_at.is_(None),
        )
        .order_by(OtpCode.id.desc())
        .limit(1)
        .with_for_update()
    )
    if record is None:
        raise OtpError("Код не найден")

    if record.expires_at <= now:
        record.consumed_at = now
        raise OtpError("Код истёк")

    if record.attempts >= settings.otp_max_attempts:
        record.consumed_at = now
        raise OtpError("Исчерпаны попытки")

    expected = hash_code(settings, email, code)
    # compare_digest: сравнение не должно зависеть от позиции первой ошибки.
    if not hmac.compare_digest(record.code_hash, expected):
        record.attempts += 1
        raise OtpError("Неверный код")

    record.consumed_at = now
    return record


async def send_code(
    db: AsyncSession,
    settings: Settings,
    mailer: Any,
    redis: Any,
    email: str,
    purpose: Purpose,
    password_hash: str | None = None,
) -> None:
    """Выпустить код и отправить письмо.

    Живёт здесь, а не в роутере входа: подтверждение по почте нужно и удалению
    аккаунта, а импортировать приватную функцию чужого роутера — худший из
    вариантов.
    """
    code = await issue_code(db, settings, email, purpose, pending_password_hash=password_hash)
    await db.commit()
    subject, body = format_message(code, purpose, settings.otp_ttl_seconds)
    await mailer.send(email, subject, body)
    if settings.otp_log_codes:
        # Только для локальной разработки: в production код в логах недопустим.
        logger.warning("OTP для %s (%s): %s", email, purpose, code)
    if settings.enable_testing_endpoints:
        # Тот же выключатель, что и у служебного роутера: без него код нигде
        # в открытом виде не сохраняется.
        from .routers.testing import testing_otp_key

        await redis.set(
            testing_otp_key(settings.key_prefix, email, purpose),
            code,
            ex=settings.otp_ttl_seconds,
        )


def format_message(code: str, purpose: Purpose, ttl_seconds: int) -> tuple[str, str]:
    """Тема письма без кода: она попадает в заголовки, уведомления и логи почты.

    Код остаётся только в теле — иначе OTP_LOG_CODES=false ничего не защищает,
    потому что заглушка-мейлер и SMTP-серверы пишут тему целиком.
    """
    action = ACTIONS[purpose]
    minutes = max(1, ttl_seconds // 60)
    subject = f"Код подтверждения {action} в Todo App"
    body = (
        f"Код подтверждения {action} в Todo App: {code}\n\n"
        f"Код действует {minutes} мин. и используется один раз.\n"
        "Если вы этого не запрашивали — просто проигнорируйте письмо."
    )
    return subject, body
