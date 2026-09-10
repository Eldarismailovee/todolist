"""Async-движок SQLAlchemy и фабрика сессий."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import get_settings

_settings = get_settings()

engine: AsyncEngine = create_async_engine(
    str(_settings.database_url),
    pool_size=_settings.db_pool_size,
    max_overflow=_settings.db_max_overflow,
    pool_pre_ping=True,
    pool_recycle=1800,
)

# expire_on_commit=False: атрибуты остаются доступны после commit без нового I/O.
# Он не подгружает связи — их нужно запрашивать явно (selectinload).
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def get_async_db() -> AsyncIterator[AsyncSession]:
    """Зависимость FastAPI: одна сессия на запрос."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Короткая сессия вне цикла запроса.

    Используется проверкой отзыва сессии внутри SSE: держать SQL-соединение весь
    срок потока нельзя.
    """
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
