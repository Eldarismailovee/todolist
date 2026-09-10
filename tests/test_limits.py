"""Лимиты тела запроса, идемпотентность, сбой публикации и rate limit."""

import pytest
from fastapi import HTTPException
from redis.exceptions import RedisError

from app import events, idempotency
from app.config import get_settings

from .conftest import bearer, create_project, fresh_access, register, set_metadata

settings = get_settings()
METADATA = [{"code": "title", "title": "Название", "type": "string", "is_required": True}]


async def _setup(client, email: str) -> int:
    await register(client, email)
    await set_metadata(METADATA)
    return await create_project(client)


async def test_oversized_body_is_rejected_before_handler(client):
    """Считаются фактически прочитанные байты, а не только Content-Length."""
    project_id = await _setup(client, "big-body@example.com")
    payload = {
        "project_id": project_id,
        "attributes": {"title": "x" * (settings.max_request_body_bytes + 1_000)},
    }

    response = await client.post(
        "/api/v1/tasks", json=payload, headers=bearer(await fresh_access(client))
    )

    assert response.status_code == 413


async def test_attributes_over_64_kib_rejected(client):
    project_id = await _setup(client, "big-attrs@example.com")
    # 40 полей по 2000 символов: тело проходит, а лимит JSONB — нет.
    attributes = {f"field_{i}": "y" * 2_000 for i in range(40)}

    response = await client.post(
        "/api/v1/tasks",
        json={"project_id": project_id, "attributes": attributes},
        headers=bearer(await fresh_access(client)),
    )

    assert response.status_code == 422


async def test_publish_failure_does_not_mask_successful_write(client, monkeypatch):
    """Задача уже сохранена: ошибка Redis не превращается в 500."""
    project_id = await _setup(client, "nopublish@example.com")

    async def broken_publish(*args, **kwargs):
        raise RedisError("публикация недоступна")

    monkeypatch.setattr(events, "publish_user_event", broken_publish)

    response = await client.post(
        "/api/v1/tasks",
        json={"project_id": project_id, "attributes": {"title": "Задача"}},
        headers=bearer(await fresh_access(client)),
    )

    assert response.status_code == 201
    listing = await client.get(
        f"/api/v1/tasks?project_id={project_id}", headers=bearer(await fresh_access(client))
    )
    assert len(listing.json()) == 1


async def test_idempotency_key_replays_the_same_result(client):
    project_id = await _setup(client, "idem@example.com")
    body = {"project_id": project_id, "attributes": {"title": "Одна задача"}}
    headers = {"Idempotency-Key": "b8b1e7c0-0000-4000-8000-000000000001"}

    first = await client.post(
        "/api/v1/tasks", json=body, headers={**headers, **bearer(await fresh_access(client))}
    )
    second = await client.post(
        "/api/v1/tasks", json=body, headers={**headers, **bearer(await fresh_access(client))}
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]

    listing = await client.get(
        f"/api/v1/tasks?project_id={project_id}", headers=bearer(await fresh_access(client))
    )
    # Повтор не создал вторую задачу.
    assert len(listing.json()) == 1


async def test_idempotency_key_with_different_body_conflicts(client):
    project_id = await _setup(client, "idem2@example.com")
    headers = {"Idempotency-Key": "b8b1e7c0-0000-4000-8000-000000000002"}

    await client.post(
        "/api/v1/tasks",
        json={"project_id": project_id, "attributes": {"title": "Первая"}},
        headers={**headers, **bearer(await fresh_access(client))},
    )
    conflict = await client.post(
        "/api/v1/tasks",
        json={"project_id": project_id, "attributes": {"title": "Другая"}},
        headers={**headers, **bearer(await fresh_access(client))},
    )

    assert conflict.status_code == 409


async def test_rejected_mutation_releases_the_idempotency_key(client):
    """Неудачная попытка не блокирует ключ навсегда."""
    project_id = await _setup(client, "idem3@example.com")
    headers = {"Idempotency-Key": "b8b1e7c0-0000-4000-8000-000000000003"}

    invalid = await client.post(
        "/api/v1/tasks",
        json={"project_id": project_id, "attributes": {"title": ""}},
        headers={**headers, **bearer(await fresh_access(client))},
    )
    retry = await client.post(
        "/api/v1/tasks",
        json={"project_id": project_id, "attributes": {"title": "Исправлено"}},
        headers={**headers, **bearer(await fresh_access(client))},
    )

    assert invalid.status_code == 422
    assert retry.status_code == 201


def test_non_ascii_idempotency_key_rejected():
    """Не-ASCII значение не проходит проверку: заголовки HTTP так не передаются."""
    with pytest.raises(HTTPException) as excinfo:
        idempotency.validate_key("ключ")
    assert excinfo.value.status_code == 400


@pytest.mark.parametrize("key", ["short", "x" * 201])
async def test_invalid_idempotency_key_rejected(client, key):
    project_id = await _setup(client, f"idem{abs(hash(key))}@example.com")

    response = await client.post(
        "/api/v1/tasks",
        json={"project_id": project_id, "attributes": {"title": "Задача"}},
        headers={"Idempotency-Key": key, **bearer(await fresh_access(client))},
    )

    assert response.status_code == 400


async def test_login_rate_limit(client):
    await register(client, "limited@example.com")

    codes = []
    for _ in range(settings.login_rate_limit + 2):
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "limited@example.com", "password": "wrong-password-here"},
        )
        codes.append(response.status_code)

    assert 401 in codes
    assert codes[-1] == 429
