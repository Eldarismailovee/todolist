"""Изоляция данных между владельцами."""

import asyncio

from httpx import ASGITransport, AsyncClient

from app.config import get_settings
from app.main import app
from app.redis_client import user_channel

from .conftest import create_project, register, set_metadata

settings = get_settings()
# Справочник описывает дополнительные поля; заголовок задачи — колонка.
METADATA = [{"code": "note", "title": "Заметка", "type": "string", "is_required": False}]


def new_client() -> AsyncClient:
    """Второй клиент: у каждого пользователя свой cookie jar."""
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Origin": "http://testserver", "X-CSRF-Guard": "1"},
        timeout=30.0,
    )


async def test_foreign_project_id_gives_404_and_publishes_nothing(client, redis_client):
    await register(client, "owner@example.com")
    await set_metadata(METADATA)
    project_id = await create_project(client, "Проект A")

    async with new_client() as intruder:
        await register(intruder, "intruder@example.com")

        async with redis_client.pubsub() as pubsub:
            await pubsub.subscribe(user_channel(settings, 1))
            response = await intruder.post(
                "/api/v1/tasks",
                json={"project_id": project_id, "title": "Чужая"},
            )
            await asyncio.sleep(0.2)
            messages = []
            while (message := await pubsub.get_message(timeout=0.2)) is not None:
                if message["type"] == "message":
                    messages.append(message)

    assert response.status_code == 404
    # Событие владельцу не публикуется: задача не создана.
    assert messages == []

    listing = await client.get(f"/api/v1/tasks?project_id={project_id}")
    assert listing.json() == []


async def test_foreign_project_is_invisible(client):
    await register(client, "a@example.com")
    project_id = await create_project(client, "Проект A")

    async with new_client() as other:
        await register(other, "b@example.com")
        direct = await other.get(f"/api/v1/projects/{project_id}")
        listing = await other.get("/api/v1/projects")
        tasks = await other.get(f"/api/v1/tasks?project_id={project_id}")

    assert direct.status_code == 404
    assert listing.json() == []
    assert tasks.status_code == 404


async def test_foreign_task_cannot_be_modified(client):
    await register(client, "owner2@example.com")
    await set_metadata(METADATA)
    project_id = await create_project(client)
    task_id = (
        await client.post(
            "/api/v1/tasks",
            json={"project_id": project_id, "title": "Моя"},
        )
    ).json()["id"]

    async with new_client() as other:
        await register(other, "other2@example.com")
        patched = await other.patch(
            f"/api/v1/tasks/{task_id}",
            json={"title": "Взломано"},
        )
        deleted = await other.request("DELETE", f"/api/v1/tasks/{task_id}")

    assert patched.status_code == 404
    assert deleted.status_code == 404

    listing = await client.get(f"/api/v1/tasks?project_id={project_id}")
    assert listing.json()[0]["title"] == "Моя"


async def test_attribute_meta_write_requires_admin(client):
    await register(client, "user@example.com")

    response = await client.post(
        "/api/v1/admin/task-attributes",
        json={"code": "hacked", "title": "X", "type": "string", "is_required": False},
    )

    assert response.status_code == 403
