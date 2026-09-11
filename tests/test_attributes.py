"""Динамическая проверка JSONB-атрибутов по серверным метаданным."""

import pytest

from .conftest import bearer, create_project, fresh_access, register, set_metadata

METADATA = [
    {"code": "label", "title": "Метка", "type": "string", "is_required": True},
    {"code": "done", "title": "Готово", "type": "boolean", "is_required": True},
    {"code": "due", "title": "Срок", "type": "date", "is_required": False},
    {"code": "note", "title": "Заметка", "type": "string", "is_required": False},
]

VALID = {"label": "Купить хлеб", "done": False, "due": "2026-09-10"}


async def _setup(client, email: str) -> int:
    await register(client, email)
    await set_metadata(METADATA)
    return await create_project(client)


async def _create(client, project_id: int, attributes: dict):
    return await client.post(
        "/api/v1/tasks",
        # Заголовок задачи — обычная колонка; attributes остаются для
        # дополнительных полей из справочника.
        json={"project_id": project_id, "title": "Задача", "attributes": attributes},
        headers=bearer(await fresh_access(client)),
    )


async def test_valid_task_is_stored(client):
    project_id = await _setup(client, "attrs@example.com")

    response = await _create(client, project_id, VALID)

    assert response.status_code == 201, response.text
    assert response.json()["attributes"] == VALID


async def test_required_boolean_false_is_accepted(client):
    """`false` — значение, а не отсутствие значения."""
    project_id = await _setup(client, "false@example.com")

    response = await _create(client, project_id, {"label": "Задача", "done": False})

    assert response.status_code == 201
    assert response.json()["attributes"]["done"] is False


async def test_optional_null_is_normalised_to_absent_key(client):
    project_id = await _setup(client, "null@example.com")

    response = await _create(
        client, project_id, {"label": "Задача", "done": True, "due": None, "note": None}
    )

    assert response.status_code == 201
    assert response.json()["attributes"] == {"label": "Задача", "done": True}


@pytest.mark.parametrize(
    ("attributes", "case"),
    [
        ({**VALID, "unknown_field": "x"}, "неизвестный ключ"),
        ({"label": "Задача", "done": "false"}, "строка вместо boolean"),
        ({"label": "Задача", "done": 0}, "число вместо boolean"),
        ({"label": "Задача", "done": True, "due": "2026-02-30"}, "несуществующая дата"),
        ({"label": "Задача", "done": True, "due": "2026-09-10T12:00:00Z"}, "timestamp"),
        ({"label": "Задача", "done": True, "due": "10.09.2026"}, "неверный формат даты"),
        ({"label": "Задача"}, "нет обязательного boolean"),
        ({"done": True}, "нет обязательной строки"),
        ({"label": "", "done": True}, "пустая обязательная строка"),
        ({"label": "x" * 2_001, "done": True}, "строка длиннее 2000"),
        ({"label": True, "done": True}, "boolean вместо строки"),
    ],
)
async def test_invalid_attributes_rejected(client, attributes, case):
    project_id = await _setup(client, f"bad{abs(hash(case))}@example.com")

    response = await _create(client, project_id, attributes)

    assert response.status_code == 422, f"{case}: {response.text}"

    listing = await client.get(
        f"/api/v1/tasks?project_id={project_id}", headers=bearer(await fresh_access(client))
    )
    # JSONB остаётся без недопустимых значений: задача не создана.
    assert listing.json() == []


async def test_too_many_attributes_rejected(client):
    project_id = await _setup(client, "many@example.com")

    response = await _create(client, project_id, {f"field_{i}": "value" for i in range(65)})

    assert response.status_code == 422


async def test_attribute_code_pattern_enforced(client):
    project_id = await _setup(client, "code@example.com")

    response = await _create(client, project_id, {"Bad-Code": "x", **VALID})

    assert response.status_code == 422


async def test_failed_update_leaves_stored_attributes_untouched(client):
    project_id = await _setup(client, "update@example.com")
    created = await _create(client, project_id, VALID)
    task_id = created.json()["id"]

    rejected = await client.patch(
        f"/api/v1/tasks/{task_id}",
        json={"attributes": {"label": "Новое", "done": "yes"}},
        headers=bearer(await fresh_access(client)),
    )

    assert rejected.status_code == 422
    listing = await client.get(
        f"/api/v1/tasks?project_id={project_id}", headers=bearer(await fresh_access(client))
    )
    assert listing.json()[0]["attributes"] == VALID


async def test_update_replaces_attribute_set(client):
    project_id = await _setup(client, "replace@example.com")
    task_id = (await _create(client, project_id, VALID)).json()["id"]

    updated = await client.patch(
        f"/api/v1/tasks/{task_id}",
        json={"attributes": {"label": "Обновлено", "done": True}},
        headers=bearer(await fresh_access(client)),
    )

    assert updated.status_code == 200
    assert updated.json()["attributes"] == {"label": "Обновлено", "done": True}


async def test_jsonb_containment_filter(client):
    """Фильтр по JSONB обслуживается GIN-индексом idx_tasks_attributes_gin."""
    project_id = await _setup(client, "filter@example.com")
    await _create(client, project_id, {"label": "Первая", "done": True})
    await _create(client, project_id, {"label": "Вторая", "done": False})

    response = await client.get(
        f'/api/v1/tasks?project_id={project_id}&attributes_contains={{"done":true}}',
        headers=bearer(await fresh_access(client)),
    )

    assert response.status_code == 200
    assert [task["attributes"]["label"] for task in response.json()] == ["Первая"]
