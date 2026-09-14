"""Сессия в cookie: авторизация, сроки, отзыв и защита от CSRF."""

import asyncio

from sqlalchemy import func, select, text, update

from app.config import get_settings
from app.cookies import session_cookie_name
from app.db import SessionLocal
from app.models import AuthSession
from app.sessions import purge_expired_sessions

from .conftest import create_project, register

settings = get_settings()
COOKIE = session_cookie_name(settings)


async def with_cookie(client, value: str, path: str = "/api/v1/projects"):
    """Запрос с конкретным значением cookie.

    Значение передаётся заголовком: cookie jar httpx приводит бездоменный хост
    `testserver` к `testserver.local`, поэтому ручная запись в jar не попала бы
    в запрос, и тест проверял бы ветку «нет cookie».
    """
    client.cookies.clear()
    return await client.get(path, headers={"Cookie": f"{COOKIE}={value}"})


async def test_session_cookie_authorizes_requests(client):
    """Токенов в теле ответа нет: запрос авторизует cookie, поставленная входом."""
    await register(client, "session@example.com")

    response = await client.get("/api/v1/projects")

    assert response.status_code == 200
    assert client.cookies.get(COOKIE)


async def test_the_same_cookie_serves_many_parallel_requests(client):
    """Обмена на одноразовый токен больше нет: параллельные запросы не в очереди."""
    await register(client, "parallel@example.com")

    responses = await asyncio.gather(*(client.get("/api/v1/projects") for _ in range(5)))

    assert [r.status_code for r in responses] == [200] * 5


async def test_session_value_is_not_exposed_to_the_client(client):
    """Значение сессии живёт только в HttpOnly cookie и хешем в базе."""
    verified = await client.post(
        "/api/v1/auth/register", json={"email": "opaque@example.com", "password": "x" * 12}
    )
    assert verified.status_code == 202

    await register(client, "opaque2@example.com")
    body = (await client.get("/api/v1/user/me")).json()

    raw = client.cookies.get(COOKIE)
    assert "access_token" not in body and "token" not in body
    async with SessionLocal() as session:
        stored = await session.scalar(select(AuthSession.token_hash))
    assert stored is not None and stored != raw


async def test_unknown_cookie_value_is_rejected(client):
    await register(client, "victim@example.com")
    valid = client.cookies.get(COOKIE)

    assert (await with_cookie(client, "z" * 43)).status_code == 401
    # Чужая попытка не гасит действующую сессию: подбором её не отозвать.
    assert (await with_cookie(client, valid)).status_code == 200


async def test_missing_cookie_is_unauthorized(client):
    assert (await client.get("/api/v1/projects")).status_code == 401


async def test_token_in_query_string_is_not_authorization(client):
    await register(client, "query@example.com")
    value = client.cookies.get(COOKIE)
    client.cookies.clear()

    response = await client.get(f"/api/v1/projects?session={value}")

    assert response.status_code == 401


async def test_absolute_expiry_ends_the_session(client):
    """Абсолютный срок активность не продлевает."""
    await register(client, "absolute@example.com")

    async with SessionLocal() as session:
        await session.execute(
            update(AuthSession).values(absolute_expires_at=text("now() - interval '1 second'"))
        )
        await session.commit()

    assert (await client.get("/api/v1/projects")).status_code == 401


async def test_idle_expiry_ends_the_session(client):
    """Простой дольше предела завершает сессию, даже если абсолютный срок цел."""
    await register(client, "idle@example.com")

    async with SessionLocal() as session:
        await session.execute(
            update(AuthSession).values(idle_expires_at=text("now() - interval '1 second'"))
        )
        await session.commit()

    assert (await client.get("/api/v1/projects")).status_code == 401


async def test_activity_extends_the_idle_deadline(client):
    """Продление происходит не чаще touch-интервала, но происходит."""
    await register(client, "touch@example.com")

    async with SessionLocal() as session:
        # Сдвигаем окно назад: следующий запрос попадает за touch-интервал.
        await session.execute(
            update(AuthSession).values(
                last_used_at=text("now() - interval '1 hour'"),
                idle_expires_at=text("now() + interval '1 minute'"),
            )
        )
        await session.commit()
        before = await session.scalar(select(AuthSession.idle_expires_at))

    assert (await client.get("/api/v1/projects")).status_code == 200

    async with SessionLocal() as session:
        after = await session.scalar(select(AuthSession.idle_expires_at))
    assert after > before


async def test_logout_revokes_session_and_clears_cookie(client):
    await register(client, "logout@example.com")
    value = client.cookies.get(COOKIE)

    response = await client.post("/api/v1/auth/logout")

    assert response.status_code == 204
    assert not client.cookies.get(COOKIE)
    # Отозвана именно сессия, а не только удалена cookie у этого клиента.
    assert (await with_cookie(client, value)).status_code == 401


async def test_logout_with_unknown_value_answers_the_same(client):
    """Иначе выходом можно было бы проверять чужие значения на существование."""
    response = await client.post("/api/v1/auth/logout", headers={"Cookie": f"{COOKIE}={'q' * 43}"})

    assert response.status_code == 204


async def test_password_change_revokes_all_sessions(client):
    await register(client, "pwd@example.com", "correct-horse-battery")
    value = client.cookies.get(COOKIE)

    response = await client.post(
        "/api/v1/user/change-password",
        json={"current_password": "correct-horse-battery", "new_password": "new-horse-battery"},
    )

    assert response.status_code == 204
    assert (await with_cookie(client, value)).status_code == 401


async def test_mutations_require_csrf_headers(client):
    """Cookie отправляет браузер сам, поэтому мутации защищены Origin и заголовком."""
    await register(client, "csrf@example.com")

    without_header = await client.post(
        "/api/v1/projects", json={"title": "Проект"}, headers={"X-CSRF-Guard": ""}
    )
    foreign_origin = await client.post(
        "/api/v1/projects", json={"title": "Проект"}, headers={"Origin": "https://evil.example"}
    )
    safe_read = await client.get("/api/v1/projects")

    assert without_header.status_code == 403
    assert foreign_origin.status_code == 403
    # Чтения не ломаются: их защищает SameSite=Strict у самой cookie.
    assert safe_read.status_code == 200


async def test_csrf_guard_covers_every_router(client):
    """Проверка висит на префиксе, а не на отдельных маршрутах."""
    await register(client, "csrf-all@example.com")
    project_id = await create_project(client)

    for method, url, payload in (
        ("POST", "/api/v1/tasks", {"project_id": project_id, "title": "Задача"}),
        ("POST", "/api/v1/tags", {"name": "тег"}),
        ("PUT", "/api/v1/notifications/settings", {"email_enabled": True}),
        ("DELETE", f"/api/v1/projects/{project_id}", None),
    ):
        response = await client.request(
            method, url, json=payload, headers={"Origin": "https://evil.example"}
        )
        assert response.status_code == 403, f"{method} {url}"


async def test_short_password_rejected(client):
    response = await client.post(
        "/api/v1/auth/register", json={"email": "short@example.com", "password": "short"}
    )
    assert response.status_code == 422


async def test_account_deletion_removes_data_and_sessions(client):
    await register(client, "gone@example.com")
    project_id = await create_project(client)
    value = client.cookies.get(COOKIE)

    response = await client.request(
        "DELETE",
        "/api/v1/user/me",
        json={"password": "correct-horse-battery"},
    )

    assert response.status_code == 204
    async with SessionLocal() as session:
        remaining = await session.scalar(
            text("SELECT count(*) FROM projects WHERE id = :id").bindparams(id=project_id)
        )
        users = await session.scalar(text("SELECT count(*) FROM users"))
    assert remaining == 0
    assert users == 0
    assert (await with_cookie(client, value)).status_code == 401


async def test_current_user_endpoint(client):
    await register(client, "whoami@example.com")

    response = await client.get("/api/v1/user/me")

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "whoami@example.com"
    assert body["is_admin"] is False
    assert "hashed_password" not in body


async def test_retention_removes_only_sessions_beyond_the_window(client):
    """Запись нужна, пока сессию можно предъявить, и не нужна после."""
    await register(client, "retention@example.com")

    async with SessionLocal() as session:
        before = await session.scalar(select(func.count()).select_from(AuthSession))
    assert before == 1

    # Действующая сессия уборкой не затрагивается.
    assert await purge_expired_sessions(settings) == 0

    async with SessionLocal() as session:
        await session.execute(
            update(AuthSession).values(
                absolute_expires_at=text(
                    f"now() - interval '{settings.session_retention_seconds + 3600} seconds'"
                ),
                idle_expires_at=text(
                    f"now() - interval '{settings.session_retention_seconds + 3600} seconds'"
                ),
            )
        )
        await session.commit()

    removed = await purge_expired_sessions(settings)

    assert removed == before
    async with SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(AuthSession)) == 0
