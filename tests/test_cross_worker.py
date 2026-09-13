"""Проверки авторизации и Pub/Sub против двух настоящих процессов приложения."""

import json

from .conftest import (
    create_project,
    live_client,
    login,
    register,
    set_metadata,
)
from .test_sse import read_event

# Справочник описывает дополнительные поля; заголовок задачи — колонка.
METADATA = [{"code": "note", "title": "Заметка", "type": "string", "is_required": False}]


async def test_session_from_one_worker_is_accepted_by_another(workers):
    """Авторитет сессии — PostgreSQL, а не память отдельного процесса."""
    async with live_client(workers[0]) as first, live_client(workers[1]) as second:
        await register(first, "portable@example.com")
        second.cookies.update(first.cookies)

        response = await second.get("/api/v1/projects")

    assert response.status_code == 200


async def test_revocation_on_one_worker_is_seen_by_another(workers):
    """Выход в одной вкладке закрывает доступ и на другом процессе."""
    async with live_client(workers[0]) as first, live_client(workers[1]) as second:
        await register(first, "revoke-cross@example.com")
        second.cookies.update(first.cookies)
        assert (await second.get("/api/v1/projects")).status_code == 200

        assert (await first.post("/api/v1/auth/logout")).status_code == 204
        after = await second.get("/api/v1/projects")

    assert after.status_code == 401


async def test_event_published_by_one_worker_reaches_stream_on_another(workers):
    async with live_client(workers[0]) as streaming, live_client(workers[1]) as writing:
        await register(streaming, "pubsub@example.com")
        await set_metadata(METADATA)
        project_id = await create_project(streaming)

        # Тот же пользователь работает со вторым воркером в отдельной сессии;
        # вход теперь двухшаговый, с подтверждением кодом.
        await login(writing, "pubsub@example.com")

        async with streaming.stream("GET", "/api/v1/tasks/stream") as response:
            lines = response.aiter_lines()
            assert (await read_event(lines))[0] == "ready"

            created = await writing.post(
                "/api/v1/tasks",
                json={"project_id": project_id, "title": "С другого воркера"},
            )
            assert created.status_code == 201

            event, data = await read_event(lines)

    assert event == "task_created"
    assert json.loads(data)["id"] == created.json()["id"]
