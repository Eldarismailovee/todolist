"""Экспорт данных: только свои данные, без секретов, без lazy loading и N+1."""

from sqlalchemy import event

from app.db import engine

from .conftest import bearer, create_project, fresh_access, register, set_metadata
from .test_isolation import new_client

METADATA = [
    {"code": "done", "title": "Готово", "type": "boolean", "is_required": False},
]


class QueryCounter:
    """Считает SQL-запросы приложения через события синхронного движка."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def __enter__(self):
        event.listen(engine.sync_engine, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *exc_info) -> None:
        event.remove(engine.sync_engine, "before_cursor_execute", self._record)

    def _record(self, conn, cursor, statement, parameters, context, executemany) -> None:
        self.statements.append(statement)

    @property
    def selects(self) -> int:
        return sum(1 for s in self.statements if s.lstrip().upper().startswith("SELECT"))


async def _seed(client, email: str, projects: int, tasks_per_project: int) -> None:
    await register(client, email)
    await set_metadata(METADATA)
    for index in range(projects):
        project_id = await create_project(client, f"Проект {index}")
        for task in range(tasks_per_project):
            response = await client.post(
                "/api/v1/tasks",
                json={
                    "project_id": project_id,
                    "title": f"Задача {task}",
                    "attributes": {"done": task % 2 == 0},
                },
                headers=bearer(await fresh_access(client)),
            )
            assert response.status_code == 201


async def test_export_contains_only_own_data_without_secrets(client):
    await _seed(client, "exporter@example.com", projects=2, tasks_per_project=2)

    async with new_client() as other:
        await register(other, "stranger@example.com")
        await create_project(other, "Чужой проект")

    response = await client.get(
        "/api/v1/user/export-data", headers=bearer(await fresh_access(client))
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["schema_version"] == 1
    assert body["user"]["email"] == "exporter@example.com"
    assert [p["title"] for p in body["projects"]] == ["Проект 0", "Проект 1"]
    assert all(len(p["tasks"]) == 2 for p in body["projects"])
    assert {m["code"] for m in body["attribute_meta"]} == {"done"}

    serialized = response.text
    for secret in ("hashed_password", "$argon2", "refresh", "access_token"):
        assert secret not in serialized


async def test_export_does_not_scale_queries_with_project_count(client):
    """selectinload: число запросов не растёт вместе с числом проектов."""
    await _seed(client, "n1@example.com", projects=2, tasks_per_project=1)
    with QueryCounter() as small:
        first = await client.get(
            "/api/v1/user/export-data", headers=bearer(await fresh_access(client))
        )

    await _seed(client, "n2@example.com", projects=6, tasks_per_project=1)
    with QueryCounter() as large:
        second = await client.get(
            "/api/v1/user/export-data", headers=bearer(await fresh_access(client))
        )

    assert first.status_code == 200 and second.status_code == 200
    assert len(second.json()["projects"]) == 6
    assert large.selects == small.selects


async def test_export_is_rejected_above_the_size_threshold(client, monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "export_max_tasks", 1)
    await _seed(client, "big@example.com", projects=1, tasks_per_project=2)

    response = await client.get(
        "/api/v1/user/export-data", headers=bearer(await fresh_access(client))
    )

    assert response.status_code == 413
