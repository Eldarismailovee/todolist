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
        # Окружение тестов задаётся полностью: локальный .env не должен менять
        # поведение. Например, поднятые для e2e лимиты превращали проверку 429
        # в сотни вычислений Argon2.
        "LOGIN_RATE_LIMIT": "10",
        "OTP_REQUEST_LIMIT": "5",
        "SECRET_KEY": "test-only-secret",
        "ENABLE_TESTING_ENDPOINTS": "false",
        "OTP_LOG_CODES": "false",
        # Внешние сервисы выключены: используются заглушки.
        "SMTP_HOST": "",
        "TELEGRAM_BOT_TOKEN": "",
        "ANTHROPIC_API_KEY": "",
        "GOOGLE_CLIENT_ID": "",
        "GITHUB_CLIENT_ID": "",
    }
)

import psycopg  # noqa: E402
import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from redis.asyncio import Redis  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402

settings = get_settings()

TABLES = (
    "task_notifications",
    "notification_prefs",
    "attachments",
    "task_tags",
    "tags",
    "categories",
    "board_columns",
    "oauth_accounts",
    "otp_codes",
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

    # Схема пересоздаётся с нуля: create_all добавляет недостающие таблицы, но
    # не меняет существующие, и тесты шли бы против устаревших колонок.
    with psycopg.connect(
        "postgresql://todo:todo@127.0.0.1:55433/todo_test", autocommit=True
    ) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")


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


OTP_CODE = "424242"


async def plant_otp(email: str, purpose: str, code: str = OTP_CODE) -> None:
    """Подменяет код у последнего непогашенного запроса на известный тесту.

    Так проверяется настоящий поток с подтверждением: письма в тестах уходят в
    заглушку, а хеш кода необратим, поэтому «подсмотреть» его иначе нельзя.
    """
    from sqlalchemy import update

    from app.models import OtpCode
    from app.otp import hash_code

    async with SessionLocal() as session:
        latest = await session.scalar(
            select(OtpCode.id)
            .where(
                OtpCode.email == email.lower(),
                OtpCode.purpose == purpose,
                OtpCode.consumed_at.is_(None),
            )
            .order_by(OtpCode.id.desc())
            .limit(1)
        )
        assert latest is not None, "Запрос кода не создал запись"
        await session.execute(
            update(OtpCode)
            .where(OtpCode.id == latest)
            .values(code_hash=hash_code(settings, email.lower(), code))
        )
        await session.commit()


async def register(http: AsyncClient, email: str, password: str = "correct-horse-battery") -> str:
    """Регистрация с подтверждением кодом; возвращает первый access token."""
    started = await http.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert started.status_code == 202, started.text
    await plant_otp(email, "register")

    verified = await http.post(
        "/api/v1/auth/otp/verify",
        json={"email": email, "code": OTP_CODE, "purpose": "register"},
    )
    assert verified.status_code == 200, verified.text
    return verified.json()["access_token"]


async def login(http: AsyncClient, email: str, password: str = "correct-horse-battery") -> str:
    """Вход с подтверждением кодом."""
    started = await http.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert started.status_code == 202, started.text
    await plant_otp(email, "login")

    verified = await http.post(
        "/api/v1/auth/otp/verify",
        json={"email": email, "code": OTP_CODE, "purpose": "login"},
    )
    assert verified.status_code == 200, verified.text
    return verified.json()["access_token"]


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


async def create_task(http: AsyncClient, project_id: int, title: str = "Задача", **fields) -> dict:
    """Создаёт задачу: заголовок теперь обычная колонка, а не JSONB-атрибут."""
    response = await http.post(
        "/api/v1/tasks",
        json={"project_id": project_id, "title": title, **fields},
        headers=bearer(await fresh_access(http)),
    )
    assert response.status_code == 201, response.text
    return response.json()


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
