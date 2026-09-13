"""Экспорт данных: только свои данные, без секретов, без lazy loading и N+1."""

import io

from sqlalchemy import event

from app.db import engine

from .conftest import PNG, create_project, register, set_metadata
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
            )
            assert response.status_code == 201


async def test_export_contains_only_own_data_without_secrets(client):
    await _seed(client, "exporter@example.com", projects=2, tasks_per_project=2)

    async with new_client() as other:
        await register(other, "stranger@example.com")
        await create_project(other, "Чужой проект")

    response = await client.get("/api/v1/user/export-data")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["schema_version"] == 2
    assert body["user"]["email"] == "exporter@example.com"
    assert [p["title"] for p in body["projects"]] == ["Проект 0", "Проект 1"]
    assert all(len(p["tasks"]) == 2 for p in body["projects"])
    assert {m["code"] for m in body["attribute_meta"]} == {"done"}

    serialized = response.text
    for secret in ("hashed_password", "$argon2", "refresh", "access_token"):
        assert secret not in serialized


async def test_export_restores_the_task_itself(client):
    """Выгрузка без заголовка, текста и срока не восстанавливает список дел."""
    await register(client, "full-export@example.com")
    await set_metadata(METADATA)
    project_id = await create_project(client, "Проект")

    tag = (await client.post("/api/v1/tags", json={"name": "срочно"})).json()
    category = (await client.post("/api/v1/categories", json={"name": "Дом"})).json()
    columns = (await client.get(f"/api/v1/board/columns?project_id={project_id}")).json()

    content = {
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Подробности"}]}],
    }
    created = await client.post(
        "/api/v1/tasks",
        json={
            "project_id": project_id,
            "title": "Заплатить за свет",
            "description": "по счётчику",
            "content": content,
            "due_at": "2026-10-01T09:00:00Z",
            "column_id": columns[0]["id"],
            "category_id": category["id"],
            "tag_ids": [tag["id"]],
            "attributes": {"done": True},
        },
    )
    assert created.status_code == 201, created.text

    body = (await client.get("/api/v1/user/export-data")).json()

    task = body["projects"][0]["tasks"][0]
    assert task["title"] == "Заплатить за свет"
    assert task["description"] == "по счётчику"
    assert task["content"] == content
    assert task["due_at"].startswith("2026-10-01T09:00:00")
    assert task["column_id"] == columns[0]["id"]
    assert task["category_id"] == category["id"]
    assert task["tag_ids"] == [tag["id"]]
    assert task["attributes"] == {"done": True}
    assert task["completed_at"] is None

    # Справочники и колонки нужны, чтобы идентификаторы задачи что-то значили.
    assert [c["name"] for c in body["categories"]] == ["Дом"]
    assert [t["name"] for t in body["tags"]] == ["срочно"]
    assert [c["id"] for c in body["projects"][0]["columns"]] == [c["id"] for c in columns]
    assert body["attachments"] == []


async def test_export_lists_attachments_without_expiring_links(client):
    """Подпись живёт час, а выгрузка хранится долго: в ней постоянные id."""
    await register(client, "export-files@example.com")
    await set_metadata(METADATA)
    uploaded = await client.post(
        "/api/v1/files",
        files={"file": ("dot.png", io.BytesIO(PNG), "image/png")},
    )
    assert uploaded.status_code == 201

    response = await client.get("/api/v1/user/export-data")
    body = response.json()

    assert [a["id"] for a in body["attachments"]] == [uploaded.json()["id"]]
    assert body["attachments"][0]["filename"] == "dot.png"
    assert "sig=" not in response.text


async def test_export_does_not_scale_queries_with_project_count(client):
    """selectinload: число запросов не растёт вместе с числом проектов."""
    await _seed(client, "n1@example.com", projects=2, tasks_per_project=1)
    with QueryCounter() as small:
        first = await client.get("/api/v1/user/export-data")

    await _seed(client, "n2@example.com", projects=6, tasks_per_project=1)
    with QueryCounter() as large:
        second = await client.get("/api/v1/user/export-data")

    assert first.status_code == 200 and second.status_code == 200
    assert len(second.json()["projects"]) == 6
    assert large.selects == small.selects


async def test_export_is_rejected_above_the_size_threshold(client, monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "export_max_tasks", 1)
    await _seed(client, "big@example.com", projects=1, tasks_per_project=2)

    response = await client.get("/api/v1/user/export-data")

    assert response.status_code == 413
