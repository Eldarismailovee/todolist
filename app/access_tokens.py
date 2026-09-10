"""Одноразовые opaque access tokens.

Токен — случайная строка с 256 битами энтропии. В Redis лежит запись по SHA-256
токена; `GETDEL` атомарно забирает и удаляет её, поэтому два одновременных
запроса с одним значением не могут авторизоваться оба — в том числе в разных
процессах приложения.
"""

import hashlib
import secrets
import time
from typing import Literal
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis
from redis.exceptions import RedisError

from .config import Settings

Purpose = Literal["api", "sse"]

# secrets.token_urlsafe(32) всегда даёт 43 символа base64url.
TOKEN_LENGTH = 43


class Principal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int = Field(gt=0)
    session_id: UUID
    purpose: Purpose
    expires_at: int


def access_key(settings: Settings, raw: str) -> str:
    digest = hashlib.sha256(raw.encode("ascii")).hexdigest()
    return f"{settings.key_prefix}auth:access:{digest}"


async def issue_access_token(
    redis: Redis,
    settings: Settings,
    user_id: int,
    session_id: UUID,
    purpose: Purpose,
) -> str:
    ttl = settings.token_ttl_seconds
    principal = Principal(
        user_id=user_id,
        session_id=session_id,
        purpose=purpose,
        expires_at=int(time.time()) + ttl,
    )
    payload = principal.model_dump_json()
    for _ in range(3):
        raw = secrets.token_urlsafe(32)
        if await redis.set(access_key(settings, raw), payload, ex=ttl, nx=True):
            return raw
    raise RuntimeError("Не удалось выделить уникальный access token")


async def revoke_access_token(redis: Redis, settings: Settings, raw: str) -> None:
    """Компенсация: снять уже выданный токен, если выдача в целом не состоялась."""
    try:
        await redis.delete(access_key(settings, raw))
    except RedisError:
        pass


async def consume_access_token(
    redis: Redis, settings: Settings, raw: str, expected_purpose: Purpose
) -> Principal:
    """Погашает токен. Повторное предъявление того же значения даёт 401."""
    if len(raw) != TOKEN_LENGTH or not raw.isascii():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Недействительный токен")
    try:
        record = await redis.getdel(access_key(settings, raw))
    except RedisError as exc:
        # Недоступность auth-хранилища — отказ в доступе, а не обход проверки.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Хранилище авторизации недоступно"
        ) from exc
    if record is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Токен истёк, уже использован или недействителен"
        )
    principal = Principal.model_validate_json(record)
    if principal.expires_at <= time.time() or principal.purpose != expected_purpose:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Недействительный токен")
    return principal
