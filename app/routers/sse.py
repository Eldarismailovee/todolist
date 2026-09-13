"""SSE-поток событий владельца.

Поток авторизуется той же сессионной cookie, что и остальные запросы: отдельный
одноразовый токен для него больше не нужен, а вместе с ним исчез и обмен
refresh перед каждым подключением. Сессия проверяется в собственной короткой
сессии БД, поэтому соединение из пула не занято всё время потока.

Поток закрывается по своему сроку (`sse_stream_seconds`); отзыв сессии
проверяется периодически внутри потока и перед каждым событием, поэтому выход
в другой вкладке обрывает поток независимо от срока cookie.
"""

import json
import logging
import time
from typing import Annotated

from fastapi import APIRouter, Depends
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sse_starlette.sse import EventSourceResponse

from ..config import Settings, get_settings
from ..dependencies import DetachedPrincipal, get_pubsub_redis
from ..redis_client import user_channel
from ..sessions import session_is_active

router = APIRouter(tags=["sse"])
logger = logging.getLogger(__name__)


@router.get("/tasks/stream")
async def tasks_sse_stream(
    principal: DetachedPrincipal,
    redis: Annotated[Redis, Depends(get_pubsub_redis)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    channel = user_channel(settings, principal.user_id)
    # Поток заведомо короче сессии: клиент переподключается, и это же
    # ограничивает время жизни занятого Redis-соединения.
    deadline = time.time() + settings.sse_stream_seconds
    revocation_interval = settings.sse_revocation_check_seconds

    async def generate():
        checked_at = time.monotonic()
        try:
            # Контекст закрывает PubSub-соединение и при отмене генератора.
            async with redis.pubsub() as pubsub:
                # subscribe() отправляет команду; ready — только после
                # подтверждения от Redis.
                await pubsub.subscribe(channel)
                while time.time() < deadline:
                    # Разрыв соединения отслеживает сам EventSourceResponse: он
                    # владеет ASGI-каналом receive, и второй потребитель того же
                    # канала мог бы перехватить сообщение о разрыве.

                    # Отзыв сессии проверяется в работающем потоке, а не только
                    # при входе. Периодичность ограничивает нагрузку на БД.
                    if time.monotonic() - checked_at >= revocation_interval:
                        checked_at = time.monotonic()
                        if not await session_is_active(principal):
                            yield {"event": "auth_revoked", "data": "{}"}
                            return

                    message = await pubsub.get_message(ignore_subscribe_messages=False, timeout=1.0)
                    if message is None or time.time() >= deadline:
                        continue

                    if message["type"] == "subscribe":
                        yield {"event": "ready", "data": "{}"}
                    elif message["type"] == "message":
                        # Проверка после ожидания: событие не уходит в уже
                        # отозванную сессию.
                        checked_at = time.monotonic()
                        if not await session_is_active(principal):
                            yield {"event": "auth_revoked", "data": "{}"}
                            return
                        event = json.loads(message["data"])
                        yield {
                            "id": event["id"],
                            "event": event["event"],
                            "data": json.dumps(event["payload"]),
                        }
        except RedisError:
            # Клиент переподключится и перечитает данные.
            logger.warning("SSE: соединение с Redis потеряно, поток закрыт")
            return

    return EventSourceResponse(
        generate(),
        ping=15,
        send_timeout=10,
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
