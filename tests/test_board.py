"""Канбан-доска: колонки и перетаскивание задач."""

from app.ordering import MIN_GAP, position_between

from .conftest import create_project, create_task, register


async def _columns(client, project_id: int) -> list[dict]:
    response = await client.get(f"/api/v1/board/columns?project_id={project_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def _move(client, task_id: int, **payload) -> dict:
    response = await client.post(
        f"/api/v1/tasks/{task_id}/move",
        json=payload,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_new_project_gets_default_columns(client):
    await register(client, "board@example.com")
    project_id = await create_project(client)

    columns = await _columns(client, project_id)

    assert [column["title"] for column in columns] == ["К выполнению", "В работе", "Готово"]
    assert [column["is_done_column"] for column in columns] == [False, False, True]
    # Позиции строго возрастают: порядок задаёт доска, а не порядок вставки.
    assert columns[0]["position"] < columns[1]["position"] < columns[2]["position"]


async def test_task_moves_between_columns(client):
    await register(client, "move@example.com")
    project_id = await create_project(client)
    columns = await _columns(client, project_id)
    task = await create_task(client, project_id, "Перетащить", column_id=columns[0]["id"])

    moved = await _move(client, task["id"], column_id=columns[1]["id"])

    assert moved["column_id"] == columns[1]["id"]
    assert moved["completed_at"] is None


async def test_moving_into_done_column_completes_task(client):
    await register(client, "done@example.com")
    project_id = await create_project(client)
    columns = await _columns(client, project_id)
    task = await create_task(client, project_id, "Закрыть", column_id=columns[0]["id"])

    done = await _move(client, task["id"], column_id=columns[2]["id"])
    reopened = await _move(client, task["id"], column_id=columns[0]["id"])

    assert done["completed_at"] is not None
    # Возврат из «Готово» снимает отметку о выполнении.
    assert reopened["completed_at"] is None


async def test_reordering_inside_column_changes_only_the_moved_task(client):
    await register(client, "order@example.com")
    project_id = await create_project(client)
    columns = await _columns(client, project_id)
    column_id = columns[0]["id"]
    first = await create_task(client, project_id, "Первая", column_id=column_id)
    second = await create_task(client, project_id, "Вторая", column_id=column_id)
    third = await create_task(client, project_id, "Третья", column_id=column_id)

    # Третью ставим между первой и второй.
    await _move(
        client, third["id"], column_id=column_id, after_id=first["id"], before_id=second["id"]
    )

    listing = await client.get(f"/api/v1/tasks?project_id={project_id}&column_id={column_id}")
    assert [task["title"] for task in listing.json()] == ["Первая", "Третья", "Вторая"]


async def test_move_to_foreign_column_is_rejected(client):
    await register(client, "foreign-column@example.com")
    first_project = await create_project(client, "Первый")
    second_project = await create_project(client, "Второй")
    other_columns = await _columns(client, second_project)
    task = await create_task(client, first_project, "Задача")

    response = await client.post(
        f"/api/v1/tasks/{task['id']}/move",
        json={"column_id": other_columns[0]["id"]},
    )

    assert response.status_code == 422


async def test_columns_can_be_renamed_and_reordered(client):
    await register(client, "columns@example.com")
    project_id = await create_project(client)
    columns = await _columns(client, project_id)

    renamed = await client.patch(
        f"/api/v1/board/columns/{columns[0]['id']}",
        json={"title": "Бэклог"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Бэклог"

    # Первую колонку переносим в конец.
    moved = await client.patch(
        f"/api/v1/board/columns/{columns[0]['id']}",
        json={"after_id": columns[2]["id"]},
    )
    assert moved.status_code == 200
    assert [column["title"] for column in await _columns(client, project_id)] == [
        "В работе",
        "Готово",
        "Бэклог",
    ]


async def test_last_column_cannot_be_deleted(client):
    await register(client, "lastcolumn@example.com")
    project_id = await create_project(client)
    columns = await _columns(client, project_id)

    for column in columns[:-1]:
        response = await client.request(
            "DELETE",
            f"/api/v1/board/columns/{column['id']}",
        )
        assert response.status_code == 204

    last = await client.request(
        "DELETE",
        f"/api/v1/board/columns/{columns[-1]['id']}",
    )
    assert last.status_code == 409


async def test_deleting_column_keeps_its_tasks(client):
    await register(client, "keeptasks@example.com")
    project_id = await create_project(client)
    columns = await _columns(client, project_id)
    await create_task(client, project_id, "Уцелевшая", column_id=columns[0]["id"])

    await client.request(
        "DELETE",
        f"/api/v1/board/columns/{columns[0]['id']}",
    )

    listing = await client.get(f"/api/v1/tasks?project_id={project_id}")
    tasks = listing.json()
    assert [task["title"] for task in tasks] == ["Уцелевшая"]
    assert tasks[0]["column_id"] is None


def test_position_between_requests_renumbering_when_gap_runs_out():
    """Когда середина между соседями неразличима, нужна перенумерация."""
    assert position_between(None, None) > 0
    assert position_between(1.0, 2.0) == 1.5
    assert position_between(None, 100.0) < 100.0
    assert position_between(100.0, None) > 100.0
    assert position_between(1.0, 1.0 + MIN_GAP / 2) is None


async def test_exhausted_gap_is_recovered_by_renumbering(client, db_session):
    """Перенумерация возвращает работоспособность без потери порядка."""
    from sqlalchemy import text

    await register(client, "renumber@example.com")
    project_id = await create_project(client)
    columns = await _columns(client, project_id)
    column_id = columns[0]["id"]
    first = await create_task(client, project_id, "A", column_id=column_id)
    second = await create_task(client, project_id, "B", column_id=column_id)
    third = await create_task(client, project_id, "C", column_id=column_id)

    # Искусственно сближаем позиции соседей до неразличимых.
    await db_session.execute(
        text("UPDATE tasks SET position = :p WHERE id = :id").bindparams(p=1.0, id=first["id"])
    )
    await db_session.execute(
        text("UPDATE tasks SET position = :p WHERE id = :id").bindparams(
            p=1.0 + MIN_GAP / 4, id=second["id"]
        )
    )
    await db_session.commit()

    await _move(
        client, third["id"], column_id=column_id, after_id=first["id"], before_id=second["id"]
    )

    listing = await client.get(f"/api/v1/tasks?project_id={project_id}&column_id={column_id}")
    assert [task["title"] for task in listing.json()] == ["A", "C", "B"]


async def test_task_without_column_goes_to_the_first_one(client):
    """Клиент может не успеть загрузить колонки — задача не должна повисать вне доски."""
    await register(client, "autocolumn@example.com")
    project_id = await create_project(client)
    columns = await _columns(client, project_id)

    task = await create_task(client, project_id, "Без указания колонки")

    assert task["column_id"] == columns[0]["id"]
