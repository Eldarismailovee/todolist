"""SSE: авторизация сессионной cookie, персональный канал и отзыв в потоке."""

import asyncio
import json

from app.config import get_settings
from app.redis_client import user_channel

from .conftest import (
    create_project,
    live_client,
    register,
    set_metadata,
)

settings = get_settings()
# Справочник описывает дополнительные поля; заголовок задачи — колонка.
METADATA = [{"code": "note", "title": "Заметка", "type": "string", "is_required": False}]


async def read_event(lines, seconds: float = 10.0) -> tuple[str, str]:
    """Собирает одно SSE-событие; комментарии-heartbeat пропускаются."""
    event, data = None, None
    while True:
        line = await asyncio.wait_for(anext(lines), seconds)
        line = line.rstrip("\r\n")
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data = line[len("data:") :].strip()
        elif line == "" and event is not None:
            return event, data or ""


async def test_stream_delivers_owner_events_only(worker_client, workers, redis_client):
    await register(worker_client, "streamer@example.com")
    await set_metadata(METADATA)
    project_id = await create_project(worker_client)

    async with live_client(workers[1]) as other:
        # Второй пользователь подключён к другому воркеру.
        await register(other, "bystander@example.com")

        async with worker_client.stream("GET", "/api/v1/tasks/stream") as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["cache-control"] == "no-store"
            lines = response.aiter_lines()

            # ready приходит только после подтверждения подписки со стороны Redis.
            event, _ = await read_event(lines)
            assert event == "ready"

            # Канал второго пользователя слушаем напрямую: туда ничего не идёт.
            async with redis_client.pubsub() as bystander_channel:
                await bystander_channel.subscribe(user_channel(settings, 2))

                created = await worker_client.post(
                    "/api/v1/tasks",
                    json={"project_id": project_id, "title": "Новая"},
                )
                assert created.status_code == 201

                event, data = await read_event(lines)
                foreign = []
                while (message := await bystander_channel.get_message(timeout=0.3)) is not None:
                    if message["type"] == "message":
                        foreign.append(message)

    assert event == "task_created"
    assert json.loads(data) == {
        "id": created.json()["id"],
        "project_id": project_id,
    }
    assert foreign == []


async def test_update_and_delete_events(worker_client):
    await register(worker_client, "events@example.com")
    await set_metadata(METADATA)
    project_id = await create_project(worker_client)
    task_id = (
        await worker_client.post(
            "/api/v1/tasks",
            json={"project_id": project_id, "title": "Задача"},
        )
    ).json()["id"]

    async with worker_client.stream("GET", "/api/v1/tasks/stream") as response:
        lines = response.aiter_lines()
        assert (await read_event(lines))[0] == "ready"

        await worker_client.patch(
            f"/api/v1/tasks/{task_id}",
            json={"title": "Изменено"},
        )
        assert (await read_event(lines))[0] == "task_updated"

        await worker_client.request("DELETE", f"/api/v1/tasks/{task_id}")
        assert (await read_event(lines))[0] == "task_deleted"


async def test_stream_can_be_reopened_with_the_same_session(worker_client):
    """Отдельного одноразового токена для подключения больше нет."""
    await register(worker_client, "reopen@example.com")

    for _ in range(2):
        async with worker_client.stream("GET", "/api/v1/tasks/stream") as response:
            assert response.status_code == 200
            assert (await read_event(response.aiter_lines()))[0] == "ready"


async def test_stream_without_session_is_rejected(worker_client):
    worker_client.cookies.clear()

    response = await worker_client.get("/api/v1/tasks/stream")

    assert response.status_code == 401


async def test_logout_ends_open_stream(worker_client):
    """Отзыв сессии проверяется в работающем потоке, а не только при входе."""
    await register(worker_client, "revoke@example.com")

    async with worker_client.stream("GET", "/api/v1/tasks/stream") as response:
        lines = response.aiter_lines()
        assert (await read_event(lines))[0] == "ready"

        assert (await worker_client.post("/api/v1/auth/logout")).status_code == 204

        event, _ = await read_event(lines, seconds=15.0)
        assert event == "auth_revoked"
        # Поток закрывается сразу после уведомления.
        assert await asyncio.wait_for(anext(lines, None), timeout=5.0) is None
