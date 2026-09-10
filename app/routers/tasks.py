"""Задачи: создание, чтение с фильтром по JSONB, обновление и удаление.

Событие публикуется только владельцу проверенного проекта и только после
успешного commit.
"""

import json

from fastapi import APIRouter, Header, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import events, idempotency
from ..attributes import load_metadata, validate_or_422
from ..dependencies import CurrentPrincipal, Db, RedisDep, SettingsDep
from ..models import Project, Task
from ..schemas import TaskCreate, TaskResponse, TaskUpdate

router = APIRouter(prefix="/tasks", tags=["tasks"])


async def _owned_project(db: AsyncSession, project_id: int, user_id: int) -> Project:
    project = await db.scalar(
        select(Project).where(Project.id == project_id, Project.owner_id == user_id)
    )
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Проект не найден")
    return project


@router.get("", response_model=list[TaskResponse])
async def list_tasks(
    principal: CurrentPrincipal,
    db: Db,
    project_id: int = Query(gt=0),
    attributes_contains: str | None = Query(
        default=None,
        max_length=2_000,
        description='JSON-фрагмент для containment-поиска по JSONB, например {"done":true}',
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    await _owned_project(db, project_id, principal.user_id)

    statement = select(Task).where(Task.project_id == project_id)
    if attributes_contains is not None:
        try:
            fragment = json.loads(attributes_contains)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "attributes_contains: некорректный JSON"
            ) from exc
        if not isinstance(fragment, dict):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "attributes_contains: ожидается объект"
            )
        # Оператор @> обслуживается GIN-индексом idx_tasks_attributes_gin.
        statement = statement.where(Task.attributes.contains(fragment))

    tasks = (await db.scalars(statement.order_by(Task.id).limit(limit).offset(offset))).all()
    return list(tasks)


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: TaskCreate,
    principal: CurrentPrincipal,
    db: Db,
    redis: RedisDep,
    settings: SettingsDep,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    key = idempotency.validate_key(idempotency_key)
    fingerprint = idempotency.body_fingerprint(payload.model_dump(mode="json"))
    if key is not None:
        replayed = await idempotency.begin(redis, settings, principal.user_id, key, fingerprint)
        if replayed is not None:
            return replayed

    try:
        project = await _owned_project(db, payload.project_id, principal.user_id)
        metadata = await load_metadata(db)
        attributes = validate_or_422(payload.attributes, metadata)

        owner_id = project.owner_id
        task = Task(project_id=project.id, attributes=attributes)
        db.add(task)
        await db.flush()
        result = TaskResponse.model_validate(task)
        await db.commit()
    except (HTTPException, SQLAlchemyError):
        # Мутация не состоялась — резерв ключа снимается, повтор разрешён.
        if key is not None:
            await idempotency.release(redis, settings, principal.user_id, key)
        raise

    if key is not None:
        await idempotency.complete(
            redis,
            settings,
            principal.user_id,
            key,
            fingerprint,
            result.model_dump(mode="json"),
        )

    await events.publish_after_commit(
        redis,
        settings,
        owner_id,
        events.TASK_CREATED,
        {"id": result.id, "project_id": result.project_id},
    )
    return result


@router.patch("/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: int,
    payload: TaskUpdate,
    principal: CurrentPrincipal,
    db: Db,
    redis: RedisDep,
    settings: SettingsDep,
):
    row = (
        await db.execute(
            select(Task, Project.owner_id)
            .join(Project, Project.id == Task.project_id)
            .where(Task.id == task_id, Project.owner_id == principal.user_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Задача не найдена")
    task, owner_id = row

    metadata = await load_metadata(db)
    # JSONB присваивается новым словарём: изменение по месту SQLAlchemy не увидит.
    task.attributes = validate_or_422(payload.attributes, metadata)
    await db.flush()
    # updated_at считает СУБД (onupdate=func.now()): значение после UPDATE
    # помечено устаревшим, и его нужно перечитать явно, а не неявным I/O.
    await db.refresh(task, attribute_names=["updated_at"])
    result = TaskResponse.model_validate(task)
    await db.commit()

    await events.publish_after_commit(
        redis,
        settings,
        owner_id,
        events.TASK_UPDATED,
        {"id": result.id, "project_id": result.project_id},
    )
    return result


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: int,
    principal: CurrentPrincipal,
    db: Db,
    redis: RedisDep,
    settings: SettingsDep,
):
    row = (
        await db.execute(
            select(Task, Project.owner_id)
            .join(Project, Project.id == Task.project_id)
            .where(Task.id == task_id, Project.owner_id == principal.user_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Задача не найдена")
    task, owner_id = row
    project_id = task.project_id

    await db.delete(task)
    await db.commit()

    await events.publish_after_commit(
        redis,
        settings,
        owner_id,
        events.TASK_DELETED,
        {"id": task_id, "project_id": project_id},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
