"""Инварианты доски: признак выполнения, условная запись, соседи, колонки.

Эти проверки описывают согласованность состояния, а не отдельный маршрут:
одно и то же пользовательское действие через PATCH и через move обязано давать
одинаковый результат, а конкурентные операции порядка — различимые позиции.
"""

import asyncio

import pytest
from sqlalchemy import delete, select

from app.models import BoardColumn

from .conftest import (
    bearer,
    create_project,
    create_task,
    fresh_access,
    live_client,
    login,
    register,
    set_metadata,
)

METADATA = [{"code": "note", "title": "Заметка", "type": "string", "is_required": False}]


async def _setup(client, email: str) -> tuple[int, list[dict]]:
    await register(client, email)
    await set_metadata(METADATA)
    project_id = await create_project(client)
    columns = (
        await client.get(
            f"/api/v1/board/columns?project_id={project_id}",
            headers=bearer(await fresh_access(client)),
        )
    ).json()
    return project_id, columns


async def _patch(client, task_id: int, body: dict, headers: dict | None = None):
    return await client.patch(
        f"/api/v1/tasks/{task_id}",
        json=body,
        headers={**bearer(await fresh_access(client)), **(headers or {})},
    )


# --- Признак выполнения --------------------------------------------------


async def test_patch_back_to_a_normal_column_reopens_the_task(client):
    """Обратный перенос через PATCH раньше оставлял задачу выполненной."""
    project_id, columns = await _setup(client, "reopen-patch@example.com")
    done = next(c for c in columns if c["is_done_column"])
    todo = next(c for c in columns if not c["is_done_column"])
    task = await create_task(client, project_id, "Задача")

    closed = await _patch(client, task["id"], {"column_id": done["id"]})
    reopened = await _patch(client, task["id"], {"column_id": todo["id"]})

    assert closed.json()["completed_at"] is not None
    assert reopened.json()["completed_at"] is None


async def test_move_out_of_the_board_reopens_the_task(client):
    """«Без колонки» — не «готово»: move с column_id=null сохранял отметку."""
    project_id, columns = await _setup(client, "reopen-move@example.com")
    done = next(c for c in columns if c["is_done_column"])
    task = await create_task(client, project_id, "Задача")
    await _patch(client, task["id"], {"column_id": done["id"]})

    moved = await client.post(
        f"/api/v1/tasks/{task['id']}/move",
        json={"column_id": None},
        headers=bearer(await fresh_access(client)),
    )

    assert moved.status_code == 200, moved.text
    assert moved.json()["column_id"] is None
    assert moved.json()["completed_at"] is None


async def test_patch_and_move_agree_on_the_done_column(client):
    """Одно действие двумя маршрутами — одно состояние."""
    project_id, columns = await _setup(client, "agree@example.com")
    done = next(c for c in columns if c["is_done_column"])
    by_patch = await create_task(client, project_id, "Через PATCH")
    by_move = await create_task(client, project_id, "Через move")

    patched = await _patch(client, by_patch["id"], {"column_id": done["id"]})
    moved = await client.post(
        f"/api/v1/tasks/{by_move['id']}/move",
        json={"column_id": done["id"]},
        headers=bearer(await fresh_access(client)),
    )

    assert (patched.json()["completed_at"] is None) == (moved.json()["completed_at"] is None)
    assert patched.json()["completed_at"] is not None


async def test_completed_flag_wins_over_the_column_in_one_request(client):
    """Явный признак применяется после колонки: запрос один, намерение одно."""
    project_id, columns = await _setup(client, "flag-wins@example.com")
    todo = next(c for c in columns if not c["is_done_column"])
    task = await create_task(client, project_id, "Задача")

    response = await _patch(client, task["id"], {"column_id": todo["id"], "completed": True})

    assert response.json()["completed_at"] is not None


# --- Условная запись -----------------------------------------------------


async def test_version_grows_with_every_change(client):
    project_id, _ = await _setup(client, "version-grows@example.com")
    task = await create_task(client, project_id, "Задача")

    first = await _patch(client, task["id"], {"title": "Первая правка"})
    second = await _patch(client, task["id"], {"title": "Вторая правка"})

    assert first.json()["version"] == task["version"] + 1
    assert second.json()["version"] == first.json()["version"] + 1


async def test_stale_if_match_does_not_overwrite_a_newer_change(client):
    """Поздний PATCH из другой вкладки молча затирал более новое изменение."""
    project_id, _ = await _setup(client, "stale@example.com")
    task = await create_task(client, project_id, "Исходный текст")
    stale_version = task["version"]

    # Первая вкладка сохранила изменение.
    await _patch(client, task["id"], {"title": "Сохранено другой вкладкой"})

    # Вторая вкладка пишет по снимку, прочитанному до этого.
    late = await _patch(
        client,
        task["id"],
        {"title": "Затирание"},
        headers={"If-Match": str(stale_version)},
    )

    assert late.status_code == 412, late.text
    current = await client.get(
        f"/api/v1/tasks?project_id={project_id}", headers=bearer(await fresh_access(client))
    )
    assert current.json()[0]["title"] == "Сохранено другой вкладкой"


@pytest.mark.parametrize("template", ["{version}", '"{version}"', 'W/"{version}"'])
async def test_matching_if_match_is_accepted_in_etag_forms(client, template):
    project_id, _ = await _setup(client, f"etag{abs(hash(template))}@example.com")
    task = await create_task(client, project_id, "Задача")

    response = await _patch(
        client,
        task["id"],
        {"title": "Новое название"},
        headers={"If-Match": template.format(version=task["version"])},
    )

    assert response.status_code == 200, response.text


# --- Соседи при перемещении ----------------------------------------------


async def test_task_cannot_be_its_own_neighbour(client):
    project_id, columns = await _setup(client, "self-neighbour@example.com")
    task = await create_task(client, project_id, "Задача")

    response = await client.post(
        f"/api/v1/tasks/{task['id']}/move",
        json={"column_id": columns[0]["id"], "before_id": task["id"]},
        headers=bearer(await fresh_access(client)),
    )

    assert response.status_code == 422, response.text


async def test_before_and_after_cannot_be_the_same_task(client):
    project_id, columns = await _setup(client, "same-neighbour@example.com")
    neighbour = await create_task(client, project_id, "Сосед")
    task = await create_task(client, project_id, "Задача")

    response = await client.post(
        f"/api/v1/tasks/{task['id']}/move",
        json={
            "column_id": columns[0]["id"],
            "before_id": neighbour["id"],
            "after_id": neighbour["id"],
        },
        headers=bearer(await fresh_access(client)),
    )

    assert response.status_code == 422, response.text


async def test_neighbours_in_the_wrong_order_are_rejected(client):
    """after_id идёт раньше before_id; обратная пара не задаёт места вставки."""
    project_id, columns = await _setup(client, "order-neighbour@example.com")
    first = await create_task(client, project_id, "Первая")
    second = await create_task(client, project_id, "Вторая")
    moved = await create_task(client, project_id, "Перемещаемая")

    response = await client.post(
        f"/api/v1/tasks/{moved['id']}/move",
        json={
            "column_id": columns[0]["id"],
            "after_id": second["id"],
            "before_id": first["id"],
        },
        headers=bearer(await fresh_access(client)),
    )

    assert response.status_code == 422, response.text


# --- Колонки -------------------------------------------------------------


async def test_listing_columns_does_not_create_them(client, db_session):
    """Чтение доски больше не пишет: два параллельных GET делали два набора."""
    project_id, _ = await _setup(client, "readonly-columns@example.com")
    await db_session.execute(delete(BoardColumn).where(BoardColumn.project_id == project_id))
    await db_session.commit()

    for _ in range(2):
        response = await client.get(
            f"/api/v1/board/columns?project_id={project_id}",
            headers=bearer(await fresh_access(client)),
        )
        assert response.json() == []

    remaining = await db_session.scalars(
        select(BoardColumn).where(BoardColumn.project_id == project_id)
    )
    assert list(remaining) == []


async def test_new_project_gets_default_columns(client):
    project_id, columns = await _setup(client, "default-columns@example.com")

    assert [c["title"] for c in columns] == ["К выполнению", "В работе", "Готово"]
    assert [c["is_done_column"] for c in columns] == [False, False, True]


async def test_parallel_deletes_cannot_remove_the_last_two_columns(workers):
    """Проверка и удаление — два запроса; без блокировки проходили обе."""
    async with live_client(workers[0]) as first, live_client(workers[1]) as second:
        await register(first, "last-column@example.com")
        await set_metadata(METADATA)
        project_id = await create_project(first)
        await login(second, "last-column@example.com")

        columns = (
            await first.get(
                f"/api/v1/board/columns?project_id={project_id}",
                headers=bearer(await fresh_access(first)),
            )
        ).json()
        # Остаются ровно две: следующая пара удалений конкурирует за последнюю.
        dropped = await first.delete(
            f"/api/v1/board/columns/{columns[0]['id']}",
            headers=bearer(await fresh_access(first)),
        )
        assert dropped.status_code == 204

        tokens = [await fresh_access(first), await fresh_access(second)]
        responses = await asyncio.gather(
            first.delete(f"/api/v1/board/columns/{columns[1]['id']}", headers=bearer(tokens[0])),
            second.delete(f"/api/v1/board/columns/{columns[2]['id']}", headers=bearer(tokens[1])),
        )

        left = (
            await first.get(
                f"/api/v1/board/columns?project_id={project_id}",
                headers=bearer(await fresh_access(first)),
            )
        ).json()

    assert sorted(r.status_code for r in responses) == [204, 409]
    assert len(left) == 1


async def test_parallel_task_creation_gets_distinct_positions(workers):
    """Позиция считается по максимуму: без блокировки обе вставки равны."""
    async with live_client(workers[0]) as first, live_client(workers[1]) as second:
        await register(first, "positions@example.com")
        await set_metadata(METADATA)
        project_id = await create_project(first)
        await login(second, "positions@example.com")

        tokens = [await fresh_access(first), await fresh_access(second)]
        created = await asyncio.gather(
            first.post(
                "/api/v1/tasks",
                json={"project_id": project_id, "title": "Первая"},
                headers=bearer(tokens[0]),
            ),
            second.post(
                "/api/v1/tasks",
                json={"project_id": project_id, "title": "Вторая"},
                headers=bearer(tokens[1]),
            ),
        )

    assert [r.status_code for r in created] == [201, 201]
    positions = {r.json()["position"] for r in created}
    assert len(positions) == 2
