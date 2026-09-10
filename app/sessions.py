"""Серверное состояние сессий: создание, проверка активности, отзыв.

Авторитет — PostgreSQL. Проверка активности открывает и закрывает собственную
короткую сессию, чтобы SSE не удерживал SQL-соединение весь срок потока.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from . import audit
from .access_tokens import Principal
from .config import Settings
from .db import session_scope
from .models import AuthSession, User


def utcnow() -> datetime:
    return datetime.now(UTC)


async def create_session(db: AsyncSession, settings: Settings, user_id: int) -> AuthSession:
    session = AuthSession(
        user_id=user_id,
        absolute_expires_at=utcnow() + timedelta(seconds=settings.session_absolute_ttl_seconds),
    )
    db.add(session)
    await db.flush()
    return session


async def _is_active(db: AsyncSession, user_id: int, session_id: UUID) -> bool:
    row = await db.execute(
        select(AuthSession.revoked_at, AuthSession.absolute_expires_at, User.is_active)
        .join(User, User.id == AuthSession.user_id)
        .where(AuthSession.id == session_id, AuthSession.user_id == user_id)
    )
    record = row.first()
    if record is None:
        return False
    revoked_at, absolute_expires_at, user_active = record
    return revoked_at is None and user_active and absolute_expires_at > utcnow()


async def session_is_active(principal: Principal) -> bool:
    """Проверка по авторитетному состоянию, с собственной короткой сессией БД."""
    async with session_scope() as db:
        return await _is_active(db, principal.user_id, principal.session_id)


async def assert_session_active(db: AsyncSession, principal: Principal) -> bool:
    """То же самое, но в уже открытой сессии запроса."""
    return await _is_active(db, principal.user_id, principal.session_id)


async def revoke_session(db: AsyncSession, session_id: UUID, reason: str) -> None:
    await db.execute(
        update(AuthSession)
        .where(AuthSession.id == session_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=utcnow(), revoked_reason=reason)
    )


async def revoke_user_sessions(
    db: AsyncSession, user_id: int, reason: str, *, keep: UUID | None = None
) -> None:
    """Отзывает все сессии пользователя: смена пароля, удаление аккаунта, logout-all."""
    statement = (
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=utcnow(), revoked_reason=reason)
    )
    if keep is not None:
        statement = statement.where(AuthSession.id != keep)
    await db.execute(statement)
    audit.add_audit(db, audit.SESSION_REVOKED, user_id=user_id, detail=reason)
