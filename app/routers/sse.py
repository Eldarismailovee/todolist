"""SSE-поток событий владельца.

Одноразовый токен назначения `sse` приходит в заголовке Authorization и уже
погашен зависимостью до открытия потока. Поток планово завершается раньше срока
токена; повторное соединение требует нового токена.
"""

import json
import logging
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sse_starlette.sse import EventSourceResponse

from ..access_tokens import Principal
from ..config import Settings, get_settings
from ..dependencies import get_pubsub_redis, get_sse_principal
from ..redis_client import user_channel
from ..sessions import session_is_active

router = APIRouter(tags=["sse"])
logger = logging.getLogger(__name__)


@router.get("/tasks/stream")
async def tasks_sse_stream(
    principal: Annotated[Principal, Depends(get_sse_principal)],
    redis: Annotated[Redis, Depends(get_pubsub_redis)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    remaining = principal.expires_at - time.time()
    if remaining < 30:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Получите новый токен для подключения")

    channel = user_channel(settings, principal.user_id)
    # Запас до истечения токена: соединение не переживает свой access token.
    deadline = min(principal.expires_at - 15, time.time() + settings.sse_stream_seconds)
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
            # Клиент переподключится с новым токеном и перечитает данные.
            logger.warning("SSE: соединение с Redis потеряно, поток закрыт")
            return

    return EventSourceResponse(
        generate(),
        ping=15,
        send_timeout=10,
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
