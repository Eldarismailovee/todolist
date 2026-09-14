"""Точка входа FastAPI. Все маршруты монтируются с общим префиксом /api/v1."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm.exc import StaleDataError

from .config import get_settings
from .db import engine
from .integrations.ai import create_assistant
from .integrations.mail import create_mailer
from .integrations.telegram import create_telegram_sender
from .logging_config import configure_logging
from .middleware import MaxBodySizeMiddleware
from .redis_client import create_redis_clients
from .routers import (
    ai,
    analytics,
    auth,
    board,
    files,
    meta,
    notifications,
    projects,
    sse,
    tasks,
    taxonomy,
    user,
)
from .security import require_csrf_guard

configure_logging()
logger = logging.getLogger(__name__)
# Опасные для production значения отвергаются здесь же: Settings не создаётся.
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Клиенты внешних сервисов создаются при старте и закрываются при остановке."""
    logger.info("Среда: %s", settings.environment)
    if settings.uses_default_secret_key:
        # В production такая конфигурация не доходит до старта, см. config.py.
        logger.warning("SECRET_KEY не задан: OTP и ссылки на файлы подписаны известным ключом")

    app.state.redis = create_redis_clients(settings)
    app.state.mailer = create_mailer(settings)
    app.state.telegram = create_telegram_sender(settings)
    app.state.assistant = create_assistant(settings)
    try:
        yield
    finally:
        await app.state.redis.aclose()
        # Клиент модели держит собственный пул HTTP-соединений.
        await app.state.assistant.aclose()
        await engine.dispose()


API_PREFIX = "/api/v1"

app = FastAPI(
    title="Todo App",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    MaxBodySizeMiddleware,
    max_bytes=settings.max_request_body_bytes,
    upload_max_bytes=settings.max_upload_body_bytes,
    upload_path=f"{API_PREFIX}{files.router.prefix}",
)
app.add_middleware(
    CORSMiddleware,
    # При credentialed CORS "*" запрещён: разрешён ровно один origin.
    allow_origins=[settings.allowed_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-CSRF-Guard", "Idempotency-Key"],
    max_age=600,
)

# CSRF-проверка висит на всём префиксе: запросы авторизуются cookie, и
# небезопасным является каждый мутирующий маршрут, а не только вход.
api = APIRouter(prefix=API_PREFIX, dependencies=[Depends(require_csrf_guard)])
api.include_router(auth.router)
api.include_router(auth.oauth_router)
api.include_router(projects.router)
api.include_router(board.router)
api.include_router(tasks.router)
api.include_router(taxonomy.router)
api.include_router(files.router)
api.include_router(ai.router)
api.include_router(analytics.router)
api.include_router(notifications.router)
api.include_router(meta.router)
api.include_router(user.router)
api.include_router(sse.router)

if settings.enable_testing_endpoints:
    # Маршрут отдаёт последний код подтверждения и существует только при
    # явно включённом флаге — в production он не регистрируется вовсе.
    from .routers import testing

    logger.warning("Включены служебные e2e-маршруты: не используйте это в production")
    api.include_router(testing.router)

app.include_router(api)


@app.exception_handler(StaleDataError)
async def handle_stale_data(request: Request, exc: StaleDataError) -> JSONResponse:
    """Условная запись не нашла строку ожидаемой версии.

    Значит, между чтением и записью её изменил другой запрос. Это конфликт
    состояния, а не сбой сервера: клиент должен перечитать задачу и повторить.
    """
    logger.info("Конфликт версий при записи: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": "Задача изменена другим запросом, обновите её и повторите"},
    )


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
