"""Задачи: создание, поиск, фильтрация, обновление, перетаскивание, удаление.

Событие публикуется только владельцу проверенного проекта и только после
успешного commit.
"""

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Header, HTTPException, Query, Response, status
from sqlalchemy import Select, func, or_, select
from sqlalchemy import delete as sql_delete
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import events, idempotency, ordering
from ..attachments import check_attachments, owned_attachments, sign_content, strip_signatures
from ..attributes import load_metadata, validate_or_422
from ..config import Settings
from ..content import ContentError, extract_text, validate_document
from ..dependencies import CurrentPrincipal, Db, RedisDep, SettingsDep
from ..errors import field_error
from ..models import BoardColumn, Category, Project, Tag, Task, TaskTag
from ..schemas import TaskCreate, TaskMove, TaskResponse, TaskUpdate

router = APIRouter(prefix="/tasks", tags=["tasks"])


async def _owned_project(db: AsyncSession, project_id: int, user_id: int) -> Project:
    project = await db.scalar(
        select(Project).where(Project.id == project_id, Project.owner_id == user_id)
    )
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Проект не найден")
    return project


async def _owned_task(db: AsyncSession, task_id: int, user_id: int) -> tuple[Task, int]:
    row = (
        await db.execute(
            select(Task, Project.owner_id)
            .join(Project, Project.id == Task.project_id)
            .options(selectinload(Task.tags))
            .where(Task.id == task_id, Project.owner_id == user_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Задача не найдена")
    return row[0], row[1]


async def _check_column(
    db: AsyncSession, column_id: int | None, project_id: int
) -> BoardColumn | None:
    if column_id is None:
        return None
    column = await db.scalar(
        select(BoardColumn).where(BoardColumn.id == column_id, BoardColumn.project_id == project_id)
    )
    if column is None:
        raise field_error(["column_id"], "Колонка не найдена в проекте")
    return column


async def _check_category(db: AsyncSession, category_id: int | None, user_id: int) -> None:
    if category_id is None:
        return
    exists = await db.scalar(
        select(Category.id).where(Category.id == category_id, Category.owner_id == user_id)
    )
    if exists is None:
        raise field_error(["category_id"], "Категория не найдена")


async def _resolve_tags(db: AsyncSession, tag_ids: list[int], user_id: int) -> list[Tag]:
    if not tag_ids:
        return []
    tags = (
        await db.scalars(select(Tag).where(Tag.id.in_(set(tag_ids)), Tag.owner_id == user_id))
    ).all()
    if len(tags) != len(set(tag_ids)):
        # Чужой тег не должен молча исчезать из запроса.
        raise field_error(["tag_ids"], "Тег не найден")
    return list(tags)


def _content_text(content: dict | None) -> str | None:
    try:
        # Сначала контракт документа, потом текст для поиска: обход по
        # непроверенной структуре молча принял бы любые узлы и атрибуты.
        validate_document(content)
        text = extract_text(content)
    except ContentError as error:
        raise field_error(["content"], str(error)) from error
    return text or None


def _column_filter(column_id: int | None):
    return Task.column_id.is_(None) if column_id is None else Task.column_id == column_id


async def _next_position(db: AsyncSession, project_id: int, column_id: int | None) -> float:
    last = await db.scalar(
        select(func.max(Task.position)).where(
            Task.project_id == project_id, _column_filter(column_id)
        )
    )
    return (last or 0.0) + ordering.STEP


def _with_tags(statement: Select) -> Select:
    # Теги подгружаются явно: связь объявлена lazy="raise".
    return statement.options(selectinload(Task.tags))


async def _responses(
    db: AsyncSession, settings: Settings, owner_id: int, tasks
) -> list[TaskResponse]:
    """Ответы с подписанными ссылками на картинки.

    В базе хранится «голый» путь: подпись живёт час и не должна попадать
    в долговременное хранилище. Владелец вложений проверяется одним запросом
    на всю выдачу — и для документов, сохранённых до этой проверки.
    """
    results = [TaskResponse.model_validate(task) for task in tasks]
    owned = await owned_attachments(db, owner_id, [result.content for result in results])
    for result in results:
        result.content = sign_content(settings, result.content, owned)
    return results


async def _response(
    db: AsyncSession, settings: Settings, owner_id: int, task: Task
) -> TaskResponse:
    return (await _responses(db, settings, owner_id, [task]))[0]


async def _replayed(db: AsyncSession, settings: Settings, owner_id: int, cached: dict) -> dict:
    """Повтор по Idempotency-Key: подписи выпускаются заново.

    Сохранённые в кэше живут час и к повтору могут истечь; заодно ответ,
    записанный до проверки владельца, не отдаст чужую подписанную ссылку.
    """
    content = strip_signatures(cached.get("content"))
    owned = await owned_attachments(db, owner_id, [content])
    return {**cached, "content": sign_content(settings, content, owned)}


@router.get("", response_model=list[TaskResponse])
async def list_tasks(
    principal: CurrentPrincipal,
    db: Db,
    settings: SettingsDep,
    project_id: int = Query(gt=0),
    q: str | None = Query(default=None, max_length=200, description="Полнотекстовый поиск"),
    tag_id: list[int] | None = Query(default=None),
    category_id: int | None = Query(default=None, gt=0),
    column_id: int | None = Query(default=None, gt=0),
    due_before: datetime | None = Query(default=None),
    completed: bool | None = Query(default=None),
    attributes_contains: str | None = Query(
        default=None,
        max_length=2_000,
        description='JSON-фрагмент для containment-поиска по JSONB, например {"billable":true}',
    ),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """Список задач проекта с поиском и фильтрами."""
    await _owned_project(db, project_id, principal.user_id)

    statement = select(Task).where(Task.project_id == project_id)

    if q:
        # websearch_to_tsquery принимает пользовательский ввод как есть:
        # кавычки и «-» не ломают запрос и не требуют экранирования.
        query = func.websearch_to_tsquery("russian", q)
        statement = statement.where(Task.search_vector.op("@@")(query)).order_by(
            func.ts_rank(Task.search_vector, query).desc(), Task.id.desc()
        )
    else:
        statement = statement.order_by(Task.position, Task.id)

    if tag_id:
        # Задача должна иметь ВСЕ выбранные теги, а не любой из них.
        wanted = set(tag_id)
        statement = statement.where(
            select(func.count(func.distinct(TaskTag.tag_id)))
            .where(TaskTag.task_id == Task.id, TaskTag.tag_id.in_(wanted))
            .scalar_subquery()
            == len(wanted)
        )
    if category_id is not None:
        statement = statement.where(Task.category_id == category_id)
    if column_id is not None:
        statement = statement.where(Task.column_id == column_id)
    if due_before is not None:
        statement = statement.where(Task.due_at.is_not(None), Task.due_at <= due_before)
    if completed is not None:
        statement = statement.where(
            Task.completed_at.is_not(None) if completed else Task.completed_at.is_(None)
        )
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

    tasks = (await db.scalars(_with_tags(statement).limit(limit).offset(offset))).all()
    return await _responses(db, settings, principal.user_id, tasks)


@router.get("/search", response_model=list[TaskResponse])
async def search_tasks(
    principal: CurrentPrincipal,
    db: Db,
    settings: SettingsDep,
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=30, ge=1, le=100),
):
    """Поиск по всем проектам пользователя: заголовок, описание, содержимое."""
    query = func.websearch_to_tsquery("russian", q)
    statement = (
        select(Task)
        .join(Project, Project.id == Task.project_id)
        .where(
            Project.owner_id == principal.user_id,
            or_(
                Task.search_vector.op("@@")(query),
                # Незаконченное слово не даёт лексемы: подстрока спасает
                # поиск по мере набора.
                Task.title.ilike(f"%{q}%"),
            ),
        )
        .order_by(func.ts_rank(Task.search_vector, query).desc(), Task.id.desc())
        .limit(limit)
    )
    tasks = (await db.scalars(_with_tags(statement))).all()
    return await _responses(db, settings, principal.user_id, tasks)


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
            return await _replayed(db, settings, principal.user_id, replayed)

    try:
        project = await _owned_project(db, payload.project_id, principal.user_id)
        column = await _check_column(db, payload.column_id, project.id)
        if column is None and payload.column_id is None:
            # Колонка не указана — берём первую на доске. Клиент может ещё не
            # успеть загрузить список колонок, и задача иначе повисла бы вне доски.
            column = await db.scalar(
                select(BoardColumn)
                .where(BoardColumn.project_id == project.id)
                .order_by(BoardColumn.position)
                .limit(1)
            )
        await _check_category(db, payload.category_id, principal.user_id)
        tags = await _resolve_tags(db, payload.tag_ids, principal.user_id)
        await check_attachments(db, principal.user_id, payload.content)

        metadata = await load_metadata(db)
        attributes = validate_or_422(payload.attributes, metadata)

        owner_id = project.owner_id
        task = Task(
            project_id=project.id,
            column_id=column.id if column else None,
            category_id=payload.category_id,
            title=payload.title,
            description=payload.description,
            content=strip_signatures(payload.content),
            content_text=_content_text(payload.content),
            due_at=payload.due_at,
            completed_at=datetime.now(UTC) if column and column.is_done_column else None,
            attributes=attributes,
            position=await _next_position(db, project.id, column.id if column else None),
        )
        task.tags = tags
        db.add(task)
        await db.flush()
        await db.refresh(task, attribute_names=["created_at", "updated_at"])
        result = await _response(db, settings, principal.user_id, task)
        await db.commit()
    except (HTTPException, SQLAlchemyError):
        # Мутация не состоялась — резерв ключа снимается, повтор разрешён.
        if key is not None:
            await idempotency.release(redis, settings, principal.user_id, key)
        raise

    if key is not None:
        await idempotency.complete(
            redis, settings, principal.user_id, key, fingerprint, result.model_dump(mode="json")
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
    task, owner_id = await _owned_task(db, task_id, principal.user_id)
    # exclude_unset: пропущенное поле и явный null — разные намерения.
    fields = payload.model_dump(exclude_unset=True)

    # Проверка на None больше не нужна: схема не пропускает null там, где
    # очистка значения не является операцией.
    if "title" in fields:
        task.title = fields["title"]
    if "description" in fields:
        task.description = fields["description"]
    if "content" in fields:
        await check_attachments(db, principal.user_id, fields["content"])
        task.content = strip_signatures(fields["content"])
        task.content_text = _content_text(fields["content"])
    if "due_at" in fields:
        task.due_at = fields["due_at"]
    if "category_id" in fields:
        await _check_category(db, fields["category_id"], principal.user_id)
        task.category_id = fields["category_id"]
    if "column_id" in fields:
        column = await _check_column(db, fields["column_id"], task.project_id)
        task.column_id = fields["column_id"]
        if column is not None and column.is_done_column and task.completed_at is None:
            task.completed_at = datetime.now(UTC)
    if "completed" in fields:
        task.completed_at = datetime.now(UTC) if fields["completed"] else None
    if "tag_ids" in fields:
        task.tags = await _resolve_tags(db, fields["tag_ids"], principal.user_id)
    if "attributes" in fields:
        metadata = await load_metadata(db)
        # JSONB присваивается новым словарём: изменение по месту SQLAlchemy не увидит.
        task.attributes = validate_or_422(fields["attributes"], metadata)

    await db.flush()
    # updated_at считает СУБД (onupdate=func.now()): значение после UPDATE
    # помечено устаревшим, и его нужно перечитать явно, а не неявным I/O.
    await db.refresh(task, attribute_names=["updated_at"])
    result = await _response(db, settings, principal.user_id, task)
    await db.commit()

    await events.publish_after_commit(
        redis,
        settings,
        owner_id,
        events.TASK_UPDATED,
        {"id": result.id, "project_id": result.project_id},
    )
    return result


@router.post("/{task_id}/move", response_model=TaskResponse)
async def move_task(
    task_id: int,
    payload: TaskMove,
    principal: CurrentPrincipal,
    db: Db,
    redis: RedisDep,
    settings: SettingsDep,
):
    """Перетаскивание: задача встаёт между указанными соседями."""
    task, owner_id = await _owned_task(db, task_id, principal.user_id)
    column = await _check_column(db, payload.column_id, task.project_id)
    target_column_id = payload.column_id

    async def neighbour_position(neighbour_id: int | None) -> float | None:
        if neighbour_id is None:
            return None
        position = await db.scalar(
            select(Task.position).where(
                Task.id == neighbour_id,
                Task.project_id == task.project_id,
                _column_filter(target_column_id),
            )
        )
        if position is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Сосед не найден в целевой колонке"
            )
        return position

    previous = await neighbour_position(payload.after_id)
    following = await neighbour_position(payload.before_id)
    position = ordering.position_between(previous, following)

    task.column_id = target_column_id
    if position is None:
        # Зазор между соседями исчерпан: перенумеровываем колонку и повторяем.
        siblings = (
            await db.scalars(
                select(Task)
                .where(Task.project_id == task.project_id, _column_filter(target_column_id))
                .order_by(Task.position, Task.id)
                .with_for_update()
            )
        ).all()
        for slot, sibling in zip(ordering.renumber(len(siblings)), siblings, strict=True):
            sibling.position = slot
        await db.flush()
        previous = await neighbour_position(payload.after_id)
        following = await neighbour_position(payload.before_id)
        position = ordering.position_between(previous, following) or ordering.STEP

    task.position = position
    if column is not None:
        # Колонка «готово» и обратно — единственный способ закрыть задачу мышью.
        task.completed_at = (
            (task.completed_at or datetime.now(UTC)) if column.is_done_column else None
        )

    await db.flush()
    await db.refresh(task, attribute_names=["updated_at"])
    result = await _response(db, settings, principal.user_id, task)
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
    task, owner_id = await _owned_task(db, task_id, principal.user_id)
    project_id = task.project_id

    await db.execute(sql_delete(Task).where(Task.id == task_id))
    await db.commit()

    await events.publish_after_commit(
        redis,
        settings,
        owner_id,
        events.TASK_DELETED,
        {"id": task_id, "project_id": project_id},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
