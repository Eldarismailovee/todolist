"""Зависимости FastAPI: Redis, внешние сервисы и авторизация по cookie сессии.

Токенов в JavaScript нет: запрос авторизуется cookie, которую браузер отправляет
сам. Поэтому здесь нет ни разбора заголовка Authorization, ни погашения
одноразовых значений — только проверка серверного состояния сессии.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings, get_settings
from .cookies import session_cookie_name
from .db import get_async_db
from .integrations.ai import Assistant
from .integrations.mail import Mailer
from .integrations.telegram import TelegramSender
from .models import User
from .sessions import Principal, authenticate, authenticate_detached

UNAUTHENTICATED = HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется вход")


def get_redis(request: Request) -> Redis:
    return request.app.state.redis.commands


def get_mailer(request: Request) -> Mailer:
    return request.app.state.mailer


def get_telegram(request: Request) -> TelegramSender:
    return request.app.state.telegram


def get_assistant(request: Request) -> Assistant:
    return request.app.state.assistant


def get_pubsub_redis(request: Request) -> Redis:
    return request.app.state.redis.pubsub


def session_token(request: Request, settings: Settings) -> str | None:
    return request.cookies.get(session_cookie_name(settings))


async def get_principal(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> Principal:
    """Владелец действующей сессии. Иначе 401."""
    principal = await authenticate(db, settings, session_token(request, settings))
    if principal is None:
        raise UNAUTHENTICATED
    return principal


async def get_optional_principal(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> Principal | None:
    """Как get_principal, но отсутствие сессии — не ошибка.

    Нужен там, где ответ зависит от того, свой ли ресурс, но публичная часть
    маршрута существует.
    """
    return await authenticate(db, settings, session_token(request, settings))


async def get_detached_principal(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Principal:
    """Проверка сессии в собственной короткой сессии БД.

    Для маршрутов, которые ждут внешний сервис или держат поток открытым:
    соединение из пула не должно быть занято всё это время.
    """
    principal = await authenticate_detached(settings, session_token(request, settings))
    if principal is None:
        raise UNAUTHENTICATED
    return principal


async def get_current_user(
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> User:
    user = await db.get(User, principal.user_id)
    if user is None or not user.is_active:
        raise UNAUTHENTICATED
    return user


async def require_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Требуются права администратора")
    return user


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
OptionalPrincipal = Annotated[Principal | None, Depends(get_optional_principal)]
# SSE и внешние вызовы: проверка сессии без удержания SQL-соединения.
DetachedPrincipal = Annotated[Principal, Depends(get_detached_principal)]
CurrentUser = Annotated[User, Depends(get_current_user)]
AdminUser = Annotated[User, Depends(require_admin)]
Db = Annotated[AsyncSession, Depends(get_async_db)]
RedisDep = Annotated[Redis, Depends(get_redis)]
MailerDep = Annotated[Mailer, Depends(get_mailer)]
TelegramDep = Annotated[TelegramSender, Depends(get_telegram)]
AssistantDep = Annotated[Assistant, Depends(get_assistant)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
