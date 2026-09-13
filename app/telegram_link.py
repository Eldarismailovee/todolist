"""Подтверждение владения Telegram-чатом.

Введённое пользователем число — не доказательство того, что чат его: с ним
можно было подписать чужой чат на свои уведомления. Поэтому сервер отправляет
код прямо в указанный чат, и подключение считается подтверждённым, только если
пользователь этот код прочитал и вернул. Возможности читать чужой чат у него
нет, а значит нет и возможности подтвердить чужой chat_id.

Полноценная привязка через одноразовый код от самого бота (`/start <код>`)
требует приёма обновлений Telegram, которого в приложении нет; здесь
используется только отправка, уже реализованная для уведомлений.
"""

import hmac
import json
import logging
from dataclasses import dataclass

from redis.asyncio import Redis

from .config import Settings
from .otp import generate_code, hash_code

logger = logging.getLogger(__name__)

# Попыток ввода на один выпущенный код: шесть цифр перебираются быстро.
MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class PendingLink:
    chat_id: str
    code_hash: str
    attempts: int


def _key(settings: Settings, user_id: int) -> str:
    return f"{settings.key_prefix}telegram:link:{user_id}"


def _subject(user_id: int) -> str:
    return f"telegram:{user_id}"


async def issue(redis: Redis, settings: Settings, user_id: int, chat_id: str) -> str:
    """Выпустить код подтверждения для чата. Предыдущий перестаёт действовать."""
    code = generate_code(settings)
    pending = {
        "chat_id": chat_id,
        "code_hash": hash_code(settings, _subject(user_id), code),
        "attempts": 0,
    }
    await redis.set(_key(settings, user_id), json.dumps(pending), ex=settings.otp_ttl_seconds)
    return code


async def confirm(redis: Redis, settings: Settings, user_id: int, code: str) -> str | None:
    """Проверить код. Возвращает подтверждённый chat_id или None.

    Попытки считаются в самой записи: после исчерпания код гасится, иначе
    шестизначное значение подбиралось бы за время его жизни.
    """
    key = _key(settings, user_id)
    raw = await redis.get(key)
    if raw is None:
        return None

    pending = json.loads(raw)
    expected = hash_code(settings, _subject(user_id), code)
    if hmac.compare_digest(pending["code_hash"], expected):
        await redis.delete(key)
        return str(pending["chat_id"])

    attempts = int(pending["attempts"]) + 1
    if attempts >= MAX_ATTEMPTS:
        await redis.delete(key)
        logger.info("Код подтверждения Telegram погашен по числу попыток, user=%s", user_id)
        return None

    pending["attempts"] = attempts
    # TTL сохраняется: срок жизни кода не должен продлеваться неудачным вводом.
    await redis.set(key, json.dumps(pending), keepttl=True)
    return None
