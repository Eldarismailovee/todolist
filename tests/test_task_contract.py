"""Контракт тела запроса задачи: часовой пояс срока и смысл явного null."""

from datetime import UTC, datetime

import pytest

from .conftest import create_project, create_task, register, set_metadata

METADATA = [{"code": "note", "title": "Заметка", "type": "string", "is_required": False}]


async def _setup(client, email: str) -> int:
    await register(client, email)
    await set_metadata(METADATA)
    return await create_project(client)


@pytest.mark.parametrize(
    ("due_at", "expected"),
    [
        ("2026-09-12T18:00:00Z", 201),
        ("2026-09-12T18:00:00+03:00", 201),
        ("2026-09-12T18:00:00-05:00", 201),
        (None, 201),
        # Без зоны момент времени не определён: его истолковала бы БД.
        ("2026-09-12T18:00:00", 422),
        ("2026-09-12", 422),
        # Число Pydantic принял бы за Unix timestamp и молча подставил UTC.
        (1789000000, 422),
    ],
)
async def test_due_at_requires_timezone(client, due_at, expected):
    project_id = await _setup(client, f"due{abs(hash(str(due_at)))}@example.com")

    response = await client.post(
        "/api/v1/tasks",
        json={"project_id": project_id, "title": "Задача", "due_at": due_at},
    )

    assert response.status_code == expected, response.text
    if expected == 422:
        assert response.json()["detail"][0]["loc"] == ["body", "due_at"]


async def test_due_at_keeps_the_sent_moment(client):
    """18:00+03:00 — это 15:00 UTC, а не 18:00 в зоне сервера или БД."""
    project_id = await _setup(client, "due-moment@example.com")
    expected = datetime(2026, 9, 12, 15, 0, tzinfo=UTC)

    created = await create_task(client, project_id, "Созвон", due_at="2026-09-12T18:00:00+03:00")
    assert datetime.fromisoformat(created["due_at"]) == expected

    # После обхода через БД момент тот же: смещение могло быть записано иначе.
    listing = await client.get(f"/api/v1/tasks?project_id={project_id}")
    assert datetime.fromisoformat(listing.json()[0]["due_at"]) == expected


@pytest.mark.parametrize("field", ["title", "tag_ids", "attributes", "completed"])
async def test_explicit_null_is_rejected_where_it_is_not_an_operation(client, field):
    """Раньше такой null молча игнорировался и выглядел как применённое изменение."""
    project_id = await _setup(client, f"null{field}@example.com")
    task = await create_task(client, project_id, "Задача")

    response = await client.patch(
        f"/api/v1/tasks/{task['id']}",
        json={field: None},
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["loc"] == ["body", field]


@pytest.mark.parametrize("field", ["description", "content", "due_at", "column_id", "category_id"])
async def test_explicit_null_clears_the_value(client, field):
    """Там, где очистка осмысленна, null остаётся допустимым."""
    project_id = await _setup(client, f"clear{field}@example.com")
    task = await create_task(
        client,
        project_id,
        "Задача",
        description="было",
        due_at="2026-09-12T18:00:00Z",
    )

    response = await client.patch(
        f"/api/v1/tasks/{task['id']}",
        json={field: None},
    )

    assert response.status_code == 200, response.text
    assert response.json()[field] is None
