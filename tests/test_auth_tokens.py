"""Одноразовость access, ротация refresh и отзыв сессий."""

import asyncio
import json

import pytest
from sqlalchemy import text

from app.access_tokens import access_key
from app.config import get_settings
from app.cookies import refresh_cookie_name
from app.db import SessionLocal

from .conftest import bearer, create_project, fresh_access, register

settings = get_settings()
COOKIE = refresh_cookie_name(settings)


async def refresh_with(client, value: str, purpose: str = "api"):
    """Обмен конкретным значением refresh.

    Значение передаётся заголовком: cookie jar httpx приводит бездоменный хост
    `testserver` к `testserver.local`, поэтому ручная запись в jar не попала бы
    в запрос, и тест проверял бы ветку «нет cookie».
    """
    client.cookies.clear()
    return await client.post(
        "/api/v1/auth/refresh",
        json={"purpose": purpose},
        headers={"Cookie": f"{COOKIE}={value}"},
    )


async def test_access_token_is_single_use(client):
    await register(client, "single@example.com")
    token = await fresh_access(client)

    first = await client.get("/api/v1/projects", headers=bearer(token))
    second = await client.get("/api/v1/projects", headers=bearer(token))

    assert first.status_code == 200
    assert second.status_code == 401


async def test_concurrent_use_of_one_access_token_lets_only_one_through(client, redis_client):
    """GETDEL атомарен: одновременное предъявление проходит ровно один раз."""
    await register(client, "race@example.com")
    token = await fresh_access(client)

    responses = await asyncio.gather(
        client.get("/api/v1/projects", headers=bearer(token)),
        client.get("/api/v1/projects", headers=bearer(token)),
    )
    codes = sorted(r.status_code for r in responses)
    assert codes == [200, 401]
    # Запись погашена, а не оставлена в Redis.
    assert await redis_client.get(access_key(settings, token)) is None


async def test_token_in_query_string_is_not_authorization(client):
    await register(client, "query@example.com")
    token = await fresh_access(client)

    response = await client.get(f"/api/v1/projects?access_token={token}")

    assert response.status_code == 401


async def test_purpose_mismatch_rejected_and_token_burned(client, redis_client):
    await register(client, "purpose@example.com")
    sse_token = await fresh_access(client, "sse")

    rest = await client.get("/api/v1/projects", headers=bearer(sse_token))

    assert rest.status_code == 401
    # Токен погашен: повторить попытку с ним нельзя даже по назначению.
    assert await redis_client.get(access_key(settings, sse_token)) is None
    stream = await client.get("/api/v1/tasks/stream", headers=bearer(sse_token))
    assert stream.status_code == 401


async def test_expired_access_rejected_regardless_of_key_ttl(client, redis_client):
    """Сервер проверяет expires_at, а не только срок жизни ключа Redis."""
    await register(client, "expired@example.com")
    token = await fresh_access(client)

    key = access_key(settings, token)
    record = json.loads(await redis_client.get(key))
    record["expires_at"] = record["expires_at"] - 10_000
    await redis_client.set(key, json.dumps(record), ex=300)

    response = await client.get("/api/v1/projects", headers=bearer(token))

    assert response.status_code == 401


async def test_missing_authorization_header(client):
    response = await client.get("/api/v1/projects")
    assert response.status_code == 401


async def test_refresh_rotates_cookie_and_issues_new_access(client):
    await register(client, "rotate@example.com")
    first_cookie = client.cookies.get(COOKIE)

    response = await client.post("/api/v1/auth/refresh", json={"purpose": "api"})

    assert response.status_code == 200
    body = response.json()
    assert body["expires_in"] == settings.access_token_ttl_seconds
    assert body["token_type"] == "Bearer"
    # Refresh token не возвращается в JSON.
    assert "refresh_token" not in body
    assert response.headers["cache-control"] == "no-store"
    assert client.cookies.get(COOKIE) != first_cookie


async def test_refresh_reuse_revokes_the_session_family(client):
    access_before = await register(client, "reuse@example.com")
    stale_cookie = client.cookies.get(COOKIE)

    assert (await client.post("/api/v1/auth/refresh", json={"purpose": "api"})).status_code == 200

    replay = await refresh_with(client, stale_cookie)
    assert replay.status_code == 401

    # Семейство отозвано: уже выданный access этой сессии тоже не даёт доступ.
    assert (await client.get("/api/v1/projects", headers=bearer(access_before))).status_code == 401


async def test_unknown_refresh_value_revokes_nothing(client):
    """Произвольный неверный токен не должен гасить чужую сессию."""
    await register(client, "victim@example.com")
    valid_cookie = client.cookies.get(COOKIE)

    assert (await refresh_with(client, "z" * 43)).status_code == 401

    assert (await refresh_with(client, valid_cookie)).status_code == 200


async def test_expired_refresh_rejected(client):
    await register(client, "oldrefresh@example.com")
    async with SessionLocal() as session:
        await session.execute(
            text("UPDATE refresh_tokens SET expires_at = now() - interval '1 second'")
        )
        await session.commit()

    response = await client.post("/api/v1/auth/refresh", json={"purpose": "api"})

    assert response.status_code == 401


async def test_concurrent_refresh_with_same_cookie_serializes(client):
    """Второй одновременный обмен тем же значением — повтор, а не вторая выдача."""
    await register(client, "parallel@example.com")

    responses = await asyncio.gather(
        client.post("/api/v1/auth/refresh", json={"purpose": "api"}),
        client.post("/api/v1/auth/refresh", json={"purpose": "api"}),
    )
    codes = sorted(r.status_code for r in responses)
    assert codes == [200, 401]


async def test_refresh_requires_csrf_headers(client):
    await register(client, "csrf@example.com")

    without_header = await client.post(
        "/api/v1/auth/refresh", json={"purpose": "api"}, headers={"X-CSRF-Guard": ""}
    )
    foreign_origin = await client.post(
        "/api/v1/auth/refresh", json={"purpose": "api"}, headers={"Origin": "https://evil.example"}
    )

    assert without_header.status_code == 403
    assert foreign_origin.status_code == 403


@pytest.mark.parametrize("payload", [{"purpose": "admin"}, {}, {"purpose": "api", "user_id": 1}])
async def test_refresh_body_is_strict(client, payload):
    await register(client, f"strict{abs(hash(str(payload)))}@example.com")
    response = await client.post("/api/v1/auth/refresh", json=payload)
    assert response.status_code == 422


async def test_logout_revokes_session_and_clears_cookie(client):
    access = await register(client, "logout@example.com")

    response = await client.post("/api/v1/auth/logout")

    assert response.status_code == 204
    assert not client.cookies.get(COOKIE)
    assert (await client.get("/api/v1/projects", headers=bearer(access))).status_code == 401


async def test_password_change_revokes_all_sessions(client):
    access = await register(client, "pwd@example.com", "correct-horse-battery")

    response = await client.post(
        "/api/v1/user/change-password",
        json={"current_password": "correct-horse-battery", "new_password": "new-horse-battery"},
        headers=bearer(await fresh_access(client)),
    )

    assert response.status_code == 204
    assert (await client.get("/api/v1/projects", headers=bearer(access))).status_code == 401
    assert (await client.post("/api/v1/auth/refresh", json={"purpose": "api"})).status_code == 401


async def test_short_password_rejected(client):
    response = await client.post(
        "/api/v1/auth/register", json={"email": "short@example.com", "password": "short"}
    )
    assert response.status_code == 422


async def test_account_deletion_removes_data_and_sessions(client):
    await register(client, "gone@example.com")
    project_id = await create_project(client)

    response = await client.request(
        "DELETE",
        "/api/v1/user/me",
        json={"password": "correct-horse-battery"},
        headers=bearer(await fresh_access(client)),
    )

    assert response.status_code == 204
    async with SessionLocal() as session:
        remaining = await session.scalar(
            text("SELECT count(*) FROM projects WHERE id = :id").bindparams(id=project_id)
        )
        users = await session.scalar(text("SELECT count(*) FROM users"))
    assert remaining == 0
    assert users == 0
    assert (await client.post("/api/v1/auth/refresh", json={"purpose": "api"})).status_code == 401


async def test_current_user_endpoint(client):
    await register(client, "whoami@example.com")

    response = await client.get("/api/v1/user/me", headers=bearer(await fresh_access(client)))

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "whoami@example.com"
    assert body["is_admin"] is False
    assert "hashed_password" not in body


async def test_retention_removes_only_records_beyond_the_window(client):
    """Записи растут на каждый запрос, но погашенный токен нужен для reuse."""
    from sqlalchemy import func, select

    from app.models import RefreshToken
    from app.worker import purge_expired_refresh_tokens

    await register(client, "retention@example.com")
    await fresh_access(client)

    async with SessionLocal() as session:
        before = await session.scalar(select(func.count()).select_from(RefreshToken))
    assert before >= 2

    # Свежие записи уборка не трогает: они ещё в окне обнаружения повтора.
    assert await purge_expired_refresh_tokens() == 0

    async with SessionLocal() as session:
        await session.execute(
            text(
                "UPDATE refresh_tokens SET expires_at = now() - "
                f"interval '{settings.refresh_retention_seconds + 3600} seconds'"
            )
        )
        await session.commit()

    removed = await purge_expired_refresh_tokens()

    assert removed == before
    async with SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(RefreshToken)) == 0
