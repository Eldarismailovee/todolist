"""Публикация событий владельцу данных через Redis Pub/Sub.

Канал определяется по проверенной серверной записи владельца; клиент канал не
выбирает. Pub/Sub не хранит сообщения для отключившегося подписчика, поэтому
событие — сигнал перечитать данные, а источник истины — PostgreSQL.
"""

import json
import logging
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError

from .config import Settings
from .redis_client import user_channel

logger = logging.getLogger(__name__)

TASK_CREATED = "task_created"
TASK_UPDATED = "task_updated"
TASK_DELETED = "task_deleted"


async def publish_user_event(
    redis: Redis, settings: Settings, owner_id: int, event_type: str, payload: dict
) -> None:
    envelope = {"id": str(uuid4()), "event": event_type, "payload": payload}
    await redis.publish(user_channel(settings, owner_id), json.dumps(envelope))


async def publish_after_commit(
    redis: Redis, settings: Settings, owner_id: int, event_type: str, payload: dict
) -> None:
    """Доставка по возможности: данные уже зафиксированы.

    Ошибка публикации не превращается в 500 — иначе клиент повторил бы уже
    выполненную мутацию. Для гарантированной доставки нужен transactional
    outbox: событие фиксируется той же транзакцией, отдельный worker публикует
    его и допускает дубликаты.
    """
    try:
        await publish_user_event(redis, settings, owner_id, event_type, payload)
    except RedisError:
        logger.warning("Изменение сохранено; realtime-уведомление недоступно (%s)", event_type)
