"""Справочник динамических атрибутов.

Чтение доступно авторизованному пользователю (по нему строится форма), запись —
только администратору. Значения метаданных никогда не принимаются от клиента
вместе с задачей.
"""

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import AdminUser, CurrentPrincipal, Db
from ..models import Task, TaskAttributeMeta
from ..schemas import MetaFieldCreate, MetaFieldResponse, parse_iso_date

router = APIRouter(tags=["attributes"])

# jsonb_typeof для типа поля из метаданных.
JSON_TYPE = {"string": "string", "date": "string", "boolean": "boolean"}


@router.get("/task-attributes", response_model=list[MetaFieldResponse])
async def list_attribute_meta(principal: CurrentPrincipal, db: Db):
    rows = (await db.scalars(select(TaskAttributeMeta).order_by(TaskAttributeMeta.code))).all()
    return list(rows)


@router.post(
    "/admin/task-attributes",
    response_model=MetaFieldResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_attribute_meta(payload: MetaFieldCreate, admin: AdminUser, db: Db):
    existing = await db.get(TaskAttributeMeta, payload.code)
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Такой код уже существует")

    if payload.is_required:
        # Новое обязательное поле сделало бы невалидными уже сохранённые задачи.
        missing = await db.scalar(
            select(func.count()).select_from(Task).where(~Task.attributes.has_key(payload.code))
        )
        if missing:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{missing} задач(и) не содержат поле {payload.code}; "
                "сначала заполните значение или добавьте поле необязательным",
            )

    meta = TaskAttributeMeta(**payload.model_dump())
    db.add(meta)
    await db.flush()
    result = MetaFieldResponse.model_validate(meta)
    await db.commit()
    return result


async def _conflicting_tasks(db: AsyncSession, code: str, field_type: str) -> int:
    """Сколько существующих задач не пройдут проверку под новым типом."""
    expected = JSON_TYPE[field_type]
    wrong_type = await db.scalar(
        select(func.count())
        .select_from(Task)
        .where(
            Task.attributes.has_key(code),
            text("jsonb_typeof(tasks.attributes -> :code) <> :expected").bindparams(
                code=code, expected=expected
            ),
        )
    )
    if wrong_type:
        return wrong_type
    if field_type != "date":
        return 0

    # Для даты тип JSON недостаточен: проверяются сами значения.
    values = (
        await db.scalars(
            select(Task.attributes[code].astext)
            .where(Task.attributes.has_key(code))
            .distinct()
            .limit(1_000)
        )
    ).all()
    invalid = 0
    for value in values:
        try:
            parse_iso_date(value)
        except ValueError:
            invalid += 1
    return invalid


@router.patch("/admin/task-attributes/{code}", response_model=MetaFieldResponse)
async def update_attribute_meta(code: str, payload: MetaFieldCreate, admin: AdminUser, db: Db):
    """Изменение типа или обязательности допускается только для совместимых данных."""
    if payload.code != code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Код поля неизменяем")
    meta = await db.get(TaskAttributeMeta, code)
    if meta is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Поле не найдено")

    if payload.type != meta.type:
        conflicts = await _conflicting_tasks(db, code, payload.type)
        if conflicts:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{conflicts} задач(и) содержат значение, несовместимое с типом {payload.type}",
            )
    if payload.is_required and not meta.is_required:
        missing = await db.scalar(
            select(func.count()).select_from(Task).where(~Task.attributes.has_key(code))
        )
        if missing:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{missing} задач(и) не содержат поле {code}",
            )

    meta.title = payload.title
    meta.type = payload.type
    meta.is_required = payload.is_required
    await db.flush()
    result = MetaFieldResponse.model_validate(meta)
    await db.commit()
    return result


@router.delete("/admin/task-attributes/{code}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attribute_meta(code: str, admin: AdminUser, db: Db):
    meta = await db.get(TaskAttributeMeta, code)
    if meta is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Поле не найдено")
    await db.delete(meta)
    await db.commit()
    # Значения в JSONB сохранённых задач остаются; новая схема их запретит при
    # следующем сохранении задачи.
    return Response(status_code=status.HTTP_204_NO_CONTENT)
