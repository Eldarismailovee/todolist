"""Лимиты тела запроса, идемпотентность, сбой публикации и rate limit."""

import io
from collections.abc import AsyncIterator

import pytest
from fastapi import HTTPException
from redis.exceptions import RedisError

from app import events, idempotency
from app.config import get_settings

from .conftest import create_project, register, set_metadata

settings = get_settings()
# Справочник описывает дополнительные поля; заголовок задачи — колонка.
METADATA = [{"code": "note", "title": "Заметка", "type": "string", "is_required": False}]


async def _setup(client, email: str) -> int:
    await register(client, email)
    await set_metadata(METADATA)
    return await create_project(client)


async def test_oversized_body_is_rejected_before_handler(client):
    """Считаются фактически прочитанные байты, а не только Content-Length."""
    project_id = await _setup(client, "big-body@example.com")
    payload = {
        "project_id": project_id,
        "title": "Большая задача",
        "description": "x" * (settings.max_request_body_bytes + 1_000),
    }

    response = await client.post("/api/v1/tasks", json=payload)

    assert response.status_code == 413


async def test_oversized_chunked_body_is_rejected(client):
    """Без Content-Length работает счётчик прочитанных байтов."""
    await _setup(client, "chunked-body@example.com")
    chunk = b"x" * (64 * 1024)
    parts = settings.max_request_body_bytes // len(chunk) + 2

    async def stream() -> AsyncIterator[bytes]:
        for _ in range(parts):
            yield chunk

    response = await client.post(
        "/api/v1/tasks",
        content=stream(),
        headers={
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 413


async def test_promised_content_size_reaches_the_handler(client):
    """Лимит содержимого в 512 KiB достижим: общий лимит тела не срабатывает раньше."""
    project_id = await _setup(client, "big-content@example.com")
    # Заметно больше прежнего общего лимита в 128 KiB и меньше полевого.
    text = "x" * (settings.max_content_bytes - 4_096)
    content = {
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
    }

    response = await client.post(
        "/api/v1/tasks",
        json={"project_id": project_id, "title": "Длинный текст", "content": content},
    )

    assert response.status_code == 201, response.text


async def test_content_over_field_limit_is_a_validation_error(client):
    """Превышение полевого лимита остаётся 422, а не превращается в 413."""
    project_id = await _setup(client, "huge-content@example.com")
    text = "x" * (settings.max_content_bytes + 1_000)
    content = {
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
    }

    response = await client.post(
        "/api/v1/tasks",
        json={"project_id": project_id, "title": "Слишком длинный текст", "content": content},
    )

    assert response.status_code == 422


# --- Лимит тела при загрузке файла ---------------------------------------
# Загрузка живёт под собственным лимитом: общий JSON-лимит отвергал бы файл
# до маршрута, и объявленные max_upload_bytes были бы недостижимы.


async def test_upload_larger_than_json_limit_is_accepted(client):
    await register(client, "upload-200k@example.com")
    payload = b"\x89PNG\r\n\x1a\n" + b"x" * (200 * 1024)

    response = await client.post(
        "/api/v1/files",
        files={"file": ("big.png", io.BytesIO(payload), "image/png")},
    )

    assert response.status_code == 201, response.text
    assert response.json()["size_bytes"] == len(payload)


async def test_upload_at_the_declared_maximum_is_accepted(client):
    """Ровно max_upload_bytes проходит: запас multipart считается сверх файла."""
    await register(client, "upload-max@example.com")
    payload = b"\x89PNG\r\n\x1a\n" + b"x" * (settings.max_upload_bytes - 8)

    response = await client.post(
        "/api/v1/files",
        files={"file": ("max.png", io.BytesIO(payload), "image/png")},
    )

    assert response.status_code == 201, response.text
    assert response.json()["size_bytes"] == settings.max_upload_bytes


async def test_upload_over_the_upload_limit_is_rejected(client):
    await register(client, "upload-over@example.com")
    payload = b"x" * (settings.max_upload_body_bytes + 1_000)

    response = await client.post(
        "/api/v1/files",
        files={"file": ("over.png", io.BytesIO(payload), "image/png")},
    )

    assert response.status_code == 413


async def test_upload_limit_does_not_apply_to_other_routes(client):
    """Послабление привязано к POST /api/v1/files, а не ко всему приложению."""
    project_id = await _setup(client, "upload-scope@example.com")
    payload = {
        "project_id": project_id,
        "title": "Задача",
        "description": "x" * (settings.max_request_body_bytes + 1_000),
    }

    response = await client.post("/api/v1/tasks", json=payload)

    assert response.status_code == 413


async def test_attributes_over_64_kib_rejected(client):
    project_id = await _setup(client, "big-attrs@example.com")
    # 40 полей по 2000 символов: тело проходит, а лимит JSONB — нет.
    attributes = {f"field_{i}": "y" * 2_000 for i in range(40)}

    response = await client.post(
        "/api/v1/tasks",
        json={"project_id": project_id, "title": "Задача", "attributes": attributes},
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
        json={"project_id": project_id, "title": "Задача"},
    )

    assert response.status_code == 201
    listing = await client.get(f"/api/v1/tasks?project_id={project_id}")
    assert len(listing.json()) == 1


async def test_idempotency_key_replays_the_same_result(client):
    project_id = await _setup(client, "idem@example.com")
    body = {"project_id": project_id, "title": "Одна задача"}
    headers = {"Idempotency-Key": "b8b1e7c0-0000-4000-8000-000000000001"}

    first = await client.post("/api/v1/tasks", json=body, headers=headers)
    second = await client.post("/api/v1/tasks", json=body, headers=headers)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]

    listing = await client.get(f"/api/v1/tasks?project_id={project_id}")
    # Повтор не создал вторую задачу.
    assert len(listing.json()) == 1


async def test_idempotency_key_with_different_body_conflicts(client):
    project_id = await _setup(client, "idem2@example.com")
    headers = {"Idempotency-Key": "b8b1e7c0-0000-4000-8000-000000000002"}

    await client.post(
        "/api/v1/tasks",
        json={"project_id": project_id, "title": "Первая"},
        headers=headers,
    )
    conflict = await client.post(
        "/api/v1/tasks",
        json={"project_id": project_id, "title": "Другая"},
        headers=headers,
    )

    assert conflict.status_code == 409


async def test_rejected_mutation_releases_the_idempotency_key(client):
    """Неудачная попытка не блокирует ключ навсегда."""
    project_id = await _setup(client, "idem3@example.com")
    headers = {"Idempotency-Key": "b8b1e7c0-0000-4000-8000-000000000003"}

    invalid = await client.post(
        "/api/v1/tasks",
        json={"project_id": project_id, "title": ""},
        headers=headers,
    )
    retry = await client.post(
        "/api/v1/tasks",
        json={"project_id": project_id, "title": "Исправлено"},
        headers=headers,
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
        json={"project_id": project_id, "title": "Задача"},
        headers={"Idempotency-Key": key},
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
