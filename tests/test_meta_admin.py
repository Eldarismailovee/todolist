"""Администрирование справочника атрибутов."""

from .conftest import bearer, create_project, fresh_access, make_admin, register, set_metadata


async def _admin(client, email: str = "admin@example.com"):
    await register(client, email)
    await make_admin(email)


async def _post_meta(client, **payload):
    return await client.post(
        "/api/v1/admin/task-attributes",
        json=payload,
        headers=bearer(await fresh_access(client)),
    )


async def test_admin_creates_field_visible_to_users(client):
    await _admin(client)

    created = await _post_meta(
        client, code="priority", title="Приоритет", type="string", is_required=False
    )
    listed = await client.get("/api/v1/task-attributes", headers=bearer(await fresh_access(client)))

    assert created.status_code == 201
    assert listed.json() == [
        {"code": "priority", "title": "Приоритет", "type": "string", "is_required": False}
    ]


async def test_duplicate_code_rejected(client):
    await _admin(client)
    await _post_meta(client, code="priority", title="Приоритет", type="string", is_required=False)

    duplicate = await _post_meta(
        client, code="priority", title="Другое", type="boolean", is_required=False
    )

    assert duplicate.status_code == 409


async def test_code_conflicting_with_basemodel_api_rejected(client):
    """`model_dump` перекрыл бы метод динамической модели."""
    await _admin(client)

    response = await _post_meta(
        client, code="model_dump", title="Плохое имя", type="string", is_required=False
    )

    assert response.status_code == 422


async def test_new_required_field_blocked_while_tasks_lack_it(client):
    await _admin(client)
    await set_metadata(
        [{"code": "label", "title": "Метка", "type": "string", "is_required": False}]
    )
    project_id = await create_project(client)
    await client.post(
        "/api/v1/tasks",
        json={"project_id": project_id, "title": "Задача", "attributes": {"label": "не дата"}},
        headers=bearer(await fresh_access(client)),
    )

    response = await _post_meta(
        client, code="owner", title="Ответственный", type="string", is_required=True
    )

    assert response.status_code == 409


async def test_type_change_blocked_for_incompatible_values(client):
    await _admin(client)
    await set_metadata(
        [{"code": "label", "title": "Метка", "type": "string", "is_required": False}]
    )
    project_id = await create_project(client)
    await client.post(
        "/api/v1/tasks",
        json={
            "project_id": project_id,
            "title": "Задача",
            "attributes": {"label": "не дата"},
        },
        headers=bearer(await fresh_access(client)),
    )

    response = await client.patch(
        "/api/v1/admin/task-attributes/label",
        json={"code": "label", "title": "Метка", "type": "date", "is_required": False},
        headers=bearer(await fresh_access(client)),
    )

    assert response.status_code == 409


async def test_type_change_allowed_when_values_fit(client):
    await _admin(client)
    await set_metadata([{"code": "when", "title": "Когда", "type": "string", "is_required": False}])
    project_id = await create_project(client)
    await client.post(
        "/api/v1/tasks",
        json={"project_id": project_id, "title": "Задача", "attributes": {"when": "2026-09-10"}},
        headers=bearer(await fresh_access(client)),
    )

    response = await client.patch(
        "/api/v1/admin/task-attributes/when",
        json={"code": "when", "title": "Когда", "type": "date", "is_required": False},
        headers=bearer(await fresh_access(client)),
    )

    assert response.status_code == 200
    assert response.json()["type"] == "date"


async def test_field_can_be_deleted(client):
    await _admin(client)
    await set_metadata(
        [{"code": "note", "title": "Заметка", "type": "string", "is_required": False}]
    )

    deleted = await client.request(
        "DELETE",
        "/api/v1/admin/task-attributes/note",
        headers=bearer(await fresh_access(client)),
    )
    listed = await client.get("/api/v1/task-attributes", headers=bearer(await fresh_access(client)))

    assert deleted.status_code == 204
    assert listed.json() == []
