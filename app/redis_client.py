"""Клиенты Redis: обычный пул и отдельный пул под Pub/Sub-подписки SSE.

Каждая SSE-подписка занимает соединение целиком, поэтому она не должна
конкурировать за пул обычных команд. Оба клиента создаются в lifespan и
закрываются при остановке приложения.
"""

from dataclasses import dataclass

from redis.asyncio import Redis

from .config import Settings


@dataclass(slots=True)
class RedisClients:
    commands: Redis
    pubsub: Redis

    async def aclose(self) -> None:
        await self.commands.aclose()
        await self.pubsub.aclose()


def create_redis_clients(settings: Settings) -> RedisClients:
    url = str(settings.redis_url)
    common = {
        "decode_responses": True,
        "socket_timeout": 5.0,
        "socket_connect_timeout": 5.0,
        "health_check_interval": 30,
    }
    return RedisClients(
        commands=Redis.from_url(url, max_connections=settings.redis_max_connections, **common),
        pubsub=Redis.from_url(
            url,
            max_connections=settings.redis_pubsub_max_connections,
            # У подписки нет собственного трафика между сообщениями: read-таймаут
            # оборвал бы простаивающий, но живой поток.
            **{**common, "socket_timeout": None},
        ),
    )


def user_channel(settings: Settings, user_id: int) -> str:
    """Канал событий одного владельца. Клиент канал не выбирает."""
    return f"{settings.key_prefix}user:{user_id}:events"
