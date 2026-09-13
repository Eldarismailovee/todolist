"""Аудит входов, выходов, отказов и отзыва сессий. Секреты не записываются."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from .db import session_scope
from .models import AuditEvent

LOGIN_OK = "login_ok"
LOGIN_FAILED = "login_failed"
LOGOUT = "logout"
SESSION_REVOKED = "session_revoked"
PASSWORD_CHANGED = "password_changed"
ACCOUNT_DELETED = "account_deleted"
ACCESS_DENIED = "access_denied"


def add_audit(
    db: AsyncSession,
    event: str,
    *,
    user_id: int | None = None,
    session_id: UUID | None = None,
    detail: str | None = None,
) -> None:
    """Добавляет событие в текущую транзакцию вызывающего кода."""
    db.add(
        AuditEvent(
            event=event,
            user_id=user_id,
            session_id=session_id,
            detail=detail[:200] if detail else None,
        )
    )


async def write_audit(
    event: str,
    *,
    user_id: int | None = None,
    session_id: UUID | None = None,
    detail: str | None = None,
) -> None:
    """Пишет событие отдельной транзакцией — переживает откат основной."""
    async with session_scope() as db:
        add_audit(db, event, user_id=user_id, session_id=session_id, detail=detail)
        await db.commit()
