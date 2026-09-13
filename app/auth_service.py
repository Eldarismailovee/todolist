"""Ротация refresh-токенов и выдача одноразовых access-токенов.

Ротация выполняется в одной транзакции с блокировкой строк
(`SELECT ... FOR UPDATE`) записи токена и сессии. Повторное предъявление
известного использованного значения отзывает всё семейство сессии; произвольный
неизвестный токен не отзывает ничего.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from fastapi import HTTPException, status
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from . import audit
from .access_tokens import Purpose, issue_access_token, revoke_access_token
from .config import Settings
from .models import AuthSession, RefreshToken, User
from .sessions import create_session, revoke_session, utcnow

INVALID_REFRESH = HTTPException(
    status.HTTP_401_UNAUTHORIZED, "Сессия недействительна, требуется повторный вход"
)
AUTH_STORE_DOWN = HTTPException(
    status.HTTP_503_SERVICE_UNAVAILABLE, "Хранилище авторизации недоступно"
)


@dataclass(slots=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    expires_in: int
    user_id: int
    session_id: UUID


def refresh_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def _new_refresh(db: AsyncSession, settings: Settings, session_id: UUID) -> str:
    raw = secrets.token_urlsafe(32)
    db.add(
        RefreshToken(
            token_hash=refresh_hash(raw),
            session_id=session_id,
            expires_at=utcnow() + timedelta(seconds=settings.refresh_token_ttl_seconds),
        )
    )
    return raw


async def _finalize(
    db: AsyncSession,
    redis: Redis,
    settings: Settings,
    user_id: int,
    session_id: UUID,
    purpose: Purpose,
    refresh_raw: str,
) -> IssuedTokens:
    """Согласует выдачу access и фиксацию ротации.

    Access создаётся до commit: сбой Redis откатывает ротацию, и старый refresh
    остаётся действительным — клиент может безопасно повторить обмен. Сбой
    commit после успешной записи в Redis снимает уже выданный access, чтобы он
    не пережил незафиксированную ротацию.
    """
    await db.flush()
    try:
        access = await issue_access_token(redis, settings, user_id, session_id, purpose)
    except RedisError as exc:
        await db.rollback()
        raise AUTH_STORE_DOWN from exc

    try:
        await db.commit()
    except SQLAlchemyError as exc:
        await revoke_access_token(redis, settings, access)
        await db.rollback()
        raise AUTH_STORE_DOWN from exc

    return IssuedTokens(
        access_token=access,
        refresh_token=refresh_raw,
        expires_in=settings.access_token_ttl_seconds,
        user_id=user_id,
        session_id=session_id,
    )


async def start_session(
    db: AsyncSession, redis: Redis, settings: Settings, user: User, purpose: Purpose = "api"
) -> IssuedTokens:
    """Вход: новая серверная сессия, первый refresh и первый access."""
    session = await create_session(db, settings, user.id)
    refresh_raw = _new_refresh(db, settings, session.id)
    audit.add_audit(db, audit.LOGIN_OK, user_id=user.id, session_id=session.id)
    return await _finalize(db, redis, settings, user.id, session.id, purpose, refresh_raw)


async def rotate_refresh(
    db: AsyncSession, redis: Redis, settings: Settings, presented: str, purpose: Purpose
) -> IssuedTokens:
    """Один обмен: старое значение гасится, выдаются новые refresh и access."""
    if not presented or len(presented) > 128 or not presented.isascii():
        raise INVALID_REFRESH

    digest = refresh_hash(presented)
    token = await db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == digest).with_for_update()
    )
    if token is None:
        # Неизвестное значение: ничего не отзываем, чтобы подбор не гасил
        # чужие сессии.
        await db.rollback()
        await audit.write_audit(audit.REFRESH_REJECTED, detail="unknown_token")
        raise INVALID_REFRESH

    session = await db.scalar(
        select(AuthSession).where(AuthSession.id == token.session_id).with_for_update()
    )
    if session is None:
        await db.rollback()
        raise INVALID_REFRESH

    now = utcnow()

    if token.used_at is not None:
        # Известное использованное значение: отзыв семейства фиксируется ДО 401
        # и не откатывается вместе с исключением.
        await revoke_session(db, session.id, "refresh_reuse")
        audit.add_audit(
            db,
            audit.REFRESH_REUSE_DETECTED,
            user_id=session.user_id,
            session_id=session.id,
        )
        await db.commit()
        raise INVALID_REFRESH

    if (
        session.revoked_at is not None
        or session.absolute_expires_at <= now
        or token.expires_at <= now
    ):
        reason = "session_revoked" if session.revoked_at else "expired"
        if session.revoked_at is None and session.absolute_expires_at <= now:
            await revoke_session(db, session.id, "absolute_timeout")
        audit.add_audit(
            db,
            audit.REFRESH_REJECTED,
            user_id=session.user_id,
            session_id=session.id,
            detail=reason,
        )
        await db.commit()
        raise INVALID_REFRESH

    user = await db.get(User, session.user_id)
    if user is None or not user.is_active:
        await revoke_session(db, session.id, "user_inactive")
        await db.commit()
        raise INVALID_REFRESH

    token.used_at = now
    session.last_used_at = now
    refresh_raw = _new_refresh(db, settings, session.id)
    return await _finalize(db, redis, settings, user.id, session.id, purpose, refresh_raw)


async def logout_by_refresh(db: AsyncSession, presented: str | None) -> bool:
    """Отзывает сессию, которой принадлежит предъявленный refresh.

    Возвращает False, если значение неизвестно: ответ всё равно 204 и cookie
    удаляется, но чужая сессия не может быть отозвана подбором.
    """
    if not presented or len(presented) > 128 or not presented.isascii():
        return False
    token = await db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == refresh_hash(presented))
    )
    if token is None:
        return False
    session = await db.get(AuthSession, token.session_id)
    if session is None:
        return False
    await revoke_session(db, session.id, "logout")
    audit.add_audit(db, audit.LOGOUT, user_id=session.user_id, session_id=session.id)
    await db.commit()
    return True
