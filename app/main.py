"""Точка входа FastAPI. Все маршруты монтируются с общим префиксом /api/v1."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .db import engine
from .middleware import MaxBodySizeMiddleware
from .redis_client import create_redis_clients
from .routers import auth, meta, projects, sse, tasks, user

logging.basicConfig(level=logging.INFO)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Redis создаётся при старте и закрывается при остановке приложения."""
    app.state.redis = create_redis_clients(settings)
    try:
        yield
    finally:
        await app.state.redis.aclose()
        await engine.dispose()


app = FastAPI(
    title="Todo App",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.add_middleware(MaxBodySizeMiddleware, max_bytes=settings.max_request_body_bytes)
app.add_middleware(
    CORSMiddleware,
    # При credentialed CORS "*" запрещён: разрешён ровно один origin.
    allow_origins=[settings.allowed_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-CSRF-Guard", "Idempotency-Key"],
    max_age=600,
)

api = APIRouter(prefix="/api/v1")
api.include_router(auth.router)
api.include_router(projects.router)
api.include_router(tasks.router)
api.include_router(meta.router)
api.include_router(user.router)
api.include_router(sse.router)
app.include_router(api)


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
