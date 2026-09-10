"""Фикстуры интеграционных тестов: настоящие PostgreSQL и Redis.

Переменные окружения выставляются до импорта приложения: движок БД и настройки
создаются на момент импорта модулей `app.*`.
"""

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

TEST_DB_URL = "postgresql+psycopg://todo:todo@127.0.0.1:55433/todo_test"

os.environ.update(
    {
        "DATABASE_URL": TEST_DB_URL,
        "REDIS_URL": "redis://127.0.0.1:56379/1",
        # Отдельный префикс: номер Redis DB не изолирует Pub/Sub.
        "REDIS_NAMESPACE": "todo:test",
        "ALLOWED_ORIGIN": "http://testserver",
        "COOKIE_SECURE": "false",
        "SSE_REVOCATION_CHECK_SECONDS": "1",
        "SSE_STREAM_SECONDS": "15",
    }
)

import psycopg  # noqa: E402
import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from redis.asyncio import Redis  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402

settings = get_settings()

TABLES = (
    "audit_events",
    "refresh_tokens",
    "auth_sessions",
    "tasks",
    "projects",
    "task_attribute_meta",
    "users",
)


def _ensure_test_database() -> None:
    with psycopg.connect(
        "postgresql://todo:todo@127.0.0.1:55433/postgres", autocommit=True
    ) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = 'todo_test'").fetchone()
        if not exists:
            conn.execute("CREATE DATABASE todo_test")


@pytest.fixture(scope="session", autouse=True)
def database() -> None:
    _ensure_test_database()


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
async def clean_state(database):
    """Схема и чистое состояние хранилищ перед каждым тестом."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))

    redis = Redis.from_url(str(settings.redis_url), decode_responses=True)
    async for key in redis.scan_iter(match=f"{settings.key_prefix}*", count=500):
        await redis.delete(key)
    await redis.aclose()
    yield


@pytest.fixture
async def redis_client():
    client = Redis.from_url(str(settings.redis_url), decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def client():
    """HTTP-клиент с запущенным lifespan приложения."""
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Origin": "http://testserver", "X-CSRF-Guard": "1"},
            timeout=30.0,
        ) as http:
            yield http


WORKER_PORTS = (8099, 8100)


def _wait_for_health(port: int, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as res:
                if res.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    raise RuntimeError(f"Воркер на порту {port} не поднялся")


@pytest.fixture(scope="session")
def workers(database) -> list[str]:
    """Два настоящих процесса uvicorn.

    Проверки авторизации и SSE должны выполняться против настоящего сервера:
    ASGI-транспорт httpx сообщает о разрыве соединения сразу после тела запроса,
    из-за чего потоковый ответ закрывался бы мгновенно.
    """
    processes = []
    for port in WORKER_PORTS:
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--log-level",
                    "warning",
                ],
                env={**os.environ},
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        )
    try:
        for port in WORKER_PORTS:
            _wait_for_health(port)
        yield [f"http://127.0.0.1:{port}" for port in WORKER_PORTS]
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            process.wait(timeout=10)


def live_client(base_url: str) -> AsyncClient:
    return AsyncClient(
        base_url=base_url,
        headers={"Origin": "http://testserver", "X-CSRF-Guard": "1"},
        timeout=30.0,
    )


@pytest.fixture
async def worker_client(workers):
    async with live_client(workers[0]) as http:
        yield http


@pytest.fixture
async def db_session():
    async with SessionLocal() as session:
        yield session


# --- Помощники -----------------------------------------------------------


async def register(http: AsyncClient, email: str, password: str = "correct-horse-battery") -> str:
    """Регистрирует пользователя и возвращает первый access token."""
    response = await http.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


async def fresh_access(http: AsyncClient, purpose: str = "api") -> str:
    """Одноразовый токен: перед каждым защищённым запросом нужен свой."""
    response = await http.post("/api/v1/auth/refresh", json={"purpose": purpose})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def create_project(http: AsyncClient, title: str = "Проект") -> int:
    response = await http.post(
        "/api/v1/projects", json={"title": title}, headers=bearer(await fresh_access(http))
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def set_metadata(rows: list[dict]) -> None:
    """Записывает справочник атрибутов напрямую: маршруты требуют администратора."""
    from app.models import TaskAttributeMeta

    async with SessionLocal() as session:
        for row in rows:
            # merge, а не add: справочник глобальный, и один тест может
            # готовить его несколько раз.
            await session.merge(TaskAttributeMeta(**row))
        await session.commit()


async def make_admin(email: str) -> None:
    """Выдаёт права администратора уже зарегистрированному пользователю."""
    from sqlalchemy import update

    from app.models import User

    async with SessionLocal() as session:
        await session.execute(update(User).where(User.email == email.lower()).values(is_admin=True))
        await session.commit()
