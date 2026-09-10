"""SSE: авторизация заголовком, персональный канал и отзыв сессии в потоке."""

import asyncio
import json
import time

from app.access_tokens import access_key
from app.config import get_settings
from app.redis_client import user_channel

from .conftest import (
    bearer,
    create_project,
    fresh_access,
    live_client,
    register,
    set_metadata,
)

settings = get_settings()
METADATA = [{"code": "title", "title": "Название", "type": "string", "is_required": True}]


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
    token = await fresh_access(worker_client, "sse")

    async with live_client(workers[1]) as other:
        # Второй пользователь подключён к другому воркеру.
        await register(other, "bystander@example.com")

        async with worker_client.stream(
            "GET", "/api/v1/tasks/stream", headers=bearer(token)
        ) as response:
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
                    json={"project_id": project_id, "attributes": {"title": "Новая"}},
                    headers=bearer(await fresh_access(worker_client)),
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
            json={"project_id": project_id, "attributes": {"title": "Задача"}},
            headers=bearer(await fresh_access(worker_client)),
        )
    ).json()["id"]
    token = await fresh_access(worker_client, "sse")

    async with worker_client.stream(
        "GET", "/api/v1/tasks/stream", headers=bearer(token)
    ) as response:
        lines = response.aiter_lines()
        assert (await read_event(lines))[0] == "ready"

        await worker_client.patch(
            f"/api/v1/tasks/{task_id}",
            json={"attributes": {"title": "Изменено"}},
            headers=bearer(await fresh_access(worker_client)),
        )
        assert (await read_event(lines))[0] == "task_updated"

        await worker_client.request(
            "DELETE", f"/api/v1/tasks/{task_id}", headers=bearer(await fresh_access(worker_client))
        )
        assert (await read_event(lines))[0] == "task_deleted"


async def test_sse_token_cannot_be_replayed(worker_client):
    await register(worker_client, "replay@example.com")
    token = await fresh_access(worker_client, "sse")

    async with worker_client.stream("GET", "/api/v1/tasks/stream", headers=bearer(token)) as first:
        assert first.status_code == 200
        assert (await read_event(first.aiter_lines()))[0] == "ready"

    second = await worker_client.get("/api/v1/tasks/stream", headers=bearer(token))
    assert second.status_code == 401

    # Новый токен снова позволяет подключиться.
    async with worker_client.stream(
        "GET", "/api/v1/tasks/stream", headers=bearer(await fresh_access(worker_client, "sse"))
    ) as third:
        assert third.status_code == 200


async def test_stream_refuses_token_that_expires_too_soon(worker_client, redis_client):
    """Соединение не должно переживать собственный access token."""
    await register(worker_client, "soon@example.com")
    token = await fresh_access(worker_client, "sse")

    key = access_key(settings, token)
    record = json.loads(await redis_client.get(key))
    # До истечения остаётся меньше 30 секунд — подключаться уже нельзя.
    record["expires_at"] = int(time.time()) + 10
    await redis_client.set(key, json.dumps(record), ex=300)

    response = await worker_client.get("/api/v1/tasks/stream", headers=bearer(token))

    assert response.status_code == 401


async def test_logout_ends_open_stream(worker_client):
    """Отзыв сессии проверяется в работающем потоке, а не только при входе."""
    await register(worker_client, "revoke@example.com")
    token = await fresh_access(worker_client, "sse")

    async with worker_client.stream(
        "GET", "/api/v1/tasks/stream", headers=bearer(token)
    ) as response:
        lines = response.aiter_lines()
        assert (await read_event(lines))[0] == "ready"

        assert (await worker_client.post("/api/v1/auth/logout")).status_code == 204

        event, _ = await read_event(lines, seconds=15.0)
        assert event == "auth_revoked"
        # Поток закрывается сразу после уведомления.
        assert await asyncio.wait_for(anext(lines, None), timeout=5.0) is None
