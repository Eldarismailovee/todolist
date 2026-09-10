"""Пароли (Argon2id), защита от CSRF и rate limit."""

from typing import Annotated

import anyio
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type
from fastapi import Depends, HTTPException, Request, status
from redis.asyncio import Redis
from redis.exceptions import RedisError

from .config import Settings, get_settings

_settings = get_settings()

_hasher = PasswordHasher(
    time_cost=_settings.argon2_time_cost,
    memory_cost=_settings.argon2_memory_cost,
    parallelism=_settings.argon2_parallelism,
    type=Type.ID,
)

# Хеш заведомо недостижимого пароля: сравнение с ним выравнивает время ответа,
# когда пользователя с таким email нет.
_DUMMY_HASH = _hasher.hash("dummy-password-for-constant-time-compare")


async def hash_password(password: str) -> str:
    """Argon2id заметно нагружает CPU — считаем в отдельном потоке."""
    return await anyio.to_thread.run_sync(_hasher.hash, password)


async def verify_password(hashed: str | None, password: str) -> bool:
    target = hashed or _DUMMY_HASH

    def _verify() -> bool:
        try:
            return _hasher.verify(target, password)
        except (VerificationError, InvalidHashError):
            return False

    matched = await anyio.to_thread.run_sync(_verify)
    return bool(matched) and hashed is not None


def needs_rehash(hashed: str) -> bool:
    try:
        return _hasher.check_needs_rehash(hashed)
    except InvalidHashError:
        return True


# --- CSRF ----------------------------------------------------------------


async def require_csrf_guard(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> None:
    """Точный Origin + обязательный пользовательский заголовок.

    Заголовок `X-CSRF-Guard` браузер не отправит в simple-запросе с другого
    origin без preflight, а preflight не пройдёт проверку CORS.
    """
    origin = request.headers.get("origin")
    if origin != settings.allowed_origin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Origin не разрешён")
    if request.headers.get("x-csrf-guard") != "1":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Отсутствует заголовок X-CSRF-Guard")


# --- Rate limit ----------------------------------------------------------


def client_ip(request: Request) -> str:
    # X-Forwarded-For доверяется только через ProxyHeadersMiddleware на границе.
    return request.client.host if request.client else "unknown"


async def enforce_rate_limit(
    redis: Redis, settings: Settings, bucket: str, limit: int, window_seconds: int
) -> None:
    """Фиксированное окно. Недоступность Redis трактуется как отказ."""
    key = f"{settings.key_prefix}ratelimit:{bucket}"
    try:
        async with redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, window_seconds, nx=True)
            hits, _ = await pipe.execute()
    except RedisError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Хранилище авторизации недоступно"
        ) from exc
    if hits > limit:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Слишком много попыток, повторите позже",
            headers={"Retry-After": str(window_seconds)},
        )
