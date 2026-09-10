"""Проверки авторизации и Pub/Sub против двух настоящих процессов приложения."""

import asyncio
import json

from .conftest import bearer, create_project, fresh_access, live_client, register, set_metadata
from .test_sse import read_event

METADATA = [{"code": "title", "title": "Название", "type": "string", "is_required": True}]


async def test_one_access_token_is_burned_across_workers(workers):
    """Погашение атомарно в Redis, а не в памяти отдельного процесса."""
    async with live_client(workers[0]) as first, live_client(workers[1]) as second:
        await register(first, "cross@example.com")
        token = await fresh_access(first)

        responses = await asyncio.gather(
            first.get("/api/v1/projects", headers=bearer(token)),
            second.get("/api/v1/projects", headers=bearer(token)),
        )

    assert sorted(r.status_code for r in responses) == [200, 401]


async def test_token_issued_on_one_worker_is_accepted_by_another(workers):
    async with live_client(workers[0]) as first, live_client(workers[1]) as second:
        await register(first, "portable@example.com")
        token = await fresh_access(first)

        response = await second.get("/api/v1/projects", headers=bearer(token))

    assert response.status_code == 200


async def test_event_published_by_one_worker_reaches_stream_on_another(workers):
    async with live_client(workers[0]) as streaming, live_client(workers[1]) as writing:
        await register(streaming, "pubsub@example.com")
        await set_metadata(METADATA)
        project_id = await create_project(streaming)

        # Тот же пользователь работает со вторым воркером в отдельной сессии.
        login = await writing.post(
            "/api/v1/auth/login",
            json={"email": "pubsub@example.com", "password": "correct-horse-battery"},
        )
        assert login.status_code == 200

        token = await fresh_access(streaming, "sse")
        async with streaming.stream(
            "GET", "/api/v1/tasks/stream", headers=bearer(token)
        ) as response:
            lines = response.aiter_lines()
            assert (await read_event(lines))[0] == "ready"

            created = await writing.post(
                "/api/v1/tasks",
                json={"project_id": project_id, "attributes": {"title": "С другого воркера"}},
                headers=bearer(await fresh_access(writing)),
            )
            assert created.status_code == 201

            event, data = await read_event(lines)

    assert event == "task_created"
    assert json.loads(data)["id"] == created.json()["id"]
