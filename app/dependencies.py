"""Зависимости FastAPI: Redis, погашение access-токена, проверка сессии."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from .access_tokens import Principal, Purpose, consume_access_token
from .config import Settings, get_settings
from .db import get_async_db
from .models import User
from .sessions import assert_session_active, session_is_active

# auto_error=False: отсутствие заголовка должно давать 401, а не 403.
bearer_scheme = HTTPBearer(auto_error=False)


def get_redis(request: Request) -> Redis:
    return request.app.state.redis.commands


def get_pubsub_redis(request: Request) -> Redis:
    return request.app.state.redis.pubsub


async def _principal_from_header(
    credentials: HTTPAuthorizationCredentials | None,
    redis: Redis,
    settings: Settings,
    purpose: Purpose,
) -> Principal:
    """Токен читается только из заголовка Authorization: query string не источник."""
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Требуется access token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await consume_access_token(redis, settings, credentials.credentials, purpose)


async def get_api_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    redis: Annotated[Redis, Depends(get_redis)],
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> Principal:
    """Погашает токен назначения `api` и проверяет, что сессия не отозвана."""
    principal = await _principal_from_header(credentials, redis, settings, "api")
    if not await assert_session_active(db, principal):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Сессия недействительна")
    return principal


async def get_sse_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    redis: Annotated[Redis, Depends(get_redis)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Principal:
    """То же для назначения `sse`.

    Сессия проверяется через собственную короткую сессию БД: SSE-ответ не должен
    удерживать SQL-соединение весь срок потока.
    """
    principal = await _principal_from_header(credentials, redis, settings, "sse")
    if not await session_is_active(principal):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Сессия недействительна")
    return principal


async def get_current_user(
    principal: Annotated[Principal, Depends(get_api_principal)],
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> User:
    user = await db.get(User, principal.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Сессия недействительна")
    return user


async def require_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Требуются права администратора")
    return user


CurrentPrincipal = Annotated[Principal, Depends(get_api_principal)]
CurrentUser = Annotated[User, Depends(get_current_user)]
AdminUser = Annotated[User, Depends(require_admin)]
Db = Annotated[AsyncSession, Depends(get_async_db)]
RedisDep = Annotated[Redis, Depends(get_redis)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
