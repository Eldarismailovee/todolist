"""Серверные сессии: выдача, проверка, продление и отзыв.

Клиент не получает никакого токена в JavaScript. Сессию подтверждает cookie
`HttpOnly; Secure; SameSite=Strict`, значение которой сервер хранит только
хешем. Авторитет состояния — PostgreSQL: отзыв действует немедленно и на всех
процессах, а не до истечения выданного токена.

Прежняя схема выдавала одноразовый access token на каждый запрос через обмен
refresh. Это превращало любые параллельные запросы в очередь сетевых обменов и
транзакций с блокировками, а на каждый запрос создавало строку в БД. Здесь
обмена нет вовсе: запрос стоит одного чтения сессии.

Проверка активности умеет открывать собственную короткую сессию БД, чтобы SSE
не удерживал SQL-соединение весь срок потока.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from . import audit
from .config import Settings
from .db import session_scope
from .models import AuthSession, User

# secrets.token_urlsafe(32) даёт 43 символа base64url; предел с запасом
# отсекает мусор до обращения к базе.
MAX_TOKEN_LENGTH = 128


class Principal(BaseModel):
    """Кто выполняет запрос. Значения ставит сервер, не клиент."""

    model_config = ConfigDict(extra="forbid")

    user_id: int = Field(gt=0)
    session_id: UUID


def utcnow() -> datetime:
    return datetime.now(UTC)


def token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


async def create_session(db: AsyncSession, settings: Settings, user_id: int) -> tuple[str, UUID]:
    """Новая сессия. Возвращает значение для cookie и её идентификатор."""
    raw = secrets.token_urlsafe(32)
    now = utcnow()
    session = AuthSession(
        user_id=user_id,
        token_hash=token_hash(raw),
        absolute_expires_at=now + timedelta(seconds=settings.session_absolute_ttl_seconds),
        idle_expires_at=now + timedelta(seconds=settings.session_idle_ttl_seconds),
    )
    db.add(session)
    await db.flush()
    audit.add_audit(db, audit.LOGIN_OK, user_id=user_id, session_id=session.id)
    return raw, session.id


def looks_like_token(raw: str | None) -> bool:
    """Отсев мусора до обращения к базе."""
    return bool(raw) and len(raw) <= MAX_TOKEN_LENGTH and raw.isascii()


async def authenticate(db: AsyncSession, settings: Settings, raw: str | None) -> Principal | None:
    """Проверить cookie и вернуть владельца сессии.

    None означает «сессии нет»: истекла, отозвана, принадлежит выключенному
    пользователю или значение не подходит вовсе. Различать эти случаи в ответе
    нельзя — иначе перебор значений сообщал бы, какие из них существуют.
    """
    if not looks_like_token(raw):
        return None

    now = utcnow()
    row = (
        await db.execute(
            select(AuthSession, User.is_active)
            .join(User, User.id == AuthSession.user_id)
            .where(AuthSession.token_hash == token_hash(raw))
        )
    ).first()
    if row is None:
        return None

    session, user_active = row
    if (
        not user_active
        or session.revoked_at is not None
        or session.absolute_expires_at <= now
        or session.idle_expires_at <= now
    ):
        return None

    await _touch(db, settings, session, now)
    return Principal(user_id=session.user_id, session_id=session.id)


async def _touch(db: AsyncSession, settings: Settings, session: AuthSession, now: datetime) -> None:
    """Продлить предел простоя, но не чаще заданного интервала.

    Запись на каждый запрос сделала бы чтение сессии мутацией и добавила бы
    блокировку строки к любым параллельным запросам одного пользователя.
    """
    interval = timedelta(seconds=settings.session_touch_interval_seconds)
    if now - session.last_used_at < interval:
        return
    await db.execute(
        update(AuthSession)
        .where(AuthSession.id == session.id)
        .values(
            last_used_at=now,
            idle_expires_at=now + timedelta(seconds=settings.session_idle_ttl_seconds),
        )
    )
    # Продление фиксируется сразу: проверка сессии выполняется до тела
    # обработчика, и чтения (которые ничего не коммитят) иначе откатывали бы
    # его вместе со своей транзакцией.
    await db.commit()


async def authenticate_detached(settings: Settings, raw: str | None) -> Principal | None:
    """То же, но в собственной короткой сессии БД.

    Нужно там, где обработчик ждёт внешний сервис или держит поток открытым:
    соединение из пула не должно быть занято всё это время.
    """
    async with session_scope() as db:
        principal = await authenticate(db, settings, raw)
        await db.commit()
        return principal


async def _is_active(db: AsyncSession, user_id: int, session_id: UUID) -> bool:
    row = await db.execute(
        select(
            AuthSession.revoked_at,
            AuthSession.absolute_expires_at,
            AuthSession.idle_expires_at,
            User.is_active,
        )
        .join(User, User.id == AuthSession.user_id)
        .where(AuthSession.id == session_id, AuthSession.user_id == user_id)
    )
    record = row.first()
    if record is None:
        return False
    revoked_at, absolute_expires_at, idle_expires_at, user_active = record
    now = utcnow()
    return (
        revoked_at is None and user_active and absolute_expires_at > now and idle_expires_at > now
    )


async def session_is_active(principal: Principal) -> bool:
    """Проверка по авторитетному состоянию, с собственной короткой сессией БД."""
    async with session_scope() as db:
        return await _is_active(db, principal.user_id, principal.session_id)


async def revoke_session(db: AsyncSession, session_id: UUID, reason: str) -> None:
    await db.execute(
        update(AuthSession)
        .where(AuthSession.id == session_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=utcnow(), revoked_reason=reason)
    )


async def revoke_by_token(db: AsyncSession, raw: str | None, reason: str) -> bool:
    """Отозвать сессию по предъявленной cookie.

    False означает, что значение неизвестно: ответ выхода всё равно успешный и
    cookie удаляется, но подбором чужую сессию отозвать нельзя.
    """
    if not looks_like_token(raw):
        return False
    session = await db.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash(raw)))
    if session is None:
        return False
    await revoke_session(db, session.id, reason)
    audit.add_audit(db, audit.LOGOUT, user_id=session.user_id, session_id=session.id)
    await db.commit()
    return True


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


async def purge_expired_sessions(settings: Settings) -> int:
    """Убрать сессии, которые уже никого не пускают.

    Строка живёт до конца абсолютного срока и после отзыва: пока сессия может
    быть предъявлена, её состояние должно быть авторитетно известно. После
    этого запись не нужна ни для чего, кроме журнала, который ведётся отдельно.
    """
    cutoff = utcnow() - timedelta(seconds=settings.session_retention_seconds)
    async with session_scope() as db:
        result = await db.execute(
            delete(AuthSession).where(
                or_(
                    AuthSession.absolute_expires_at < cutoff,
                    AuthSession.idle_expires_at < cutoff,
                )
            )
        )
        await db.commit()
    return result.rowcount or 0
