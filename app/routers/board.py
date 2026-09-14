"""Канбан-колонки: создание, переименование, перетаскивание, удаление."""

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import ordering
from ..board_service import check_neighbour_order, check_neighbours, lock_project_board
from ..dependencies import CurrentPrincipal, Db
from ..errors import field_error
from ..models import BoardColumn, Project
from ..schemas import BoardColumnCreate, BoardColumnResponse, BoardColumnUpdate

router = APIRouter(prefix="/board", tags=["board"])


async def _owned_project(db: AsyncSession, project_id: int, user_id: int) -> Project:
    project = await db.scalar(
        select(Project).where(Project.id == project_id, Project.owner_id == user_id)
    )
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Проект не найден")
    return project


async def _owned_column(db: AsyncSession, column_id: int, user_id: int) -> BoardColumn:
    column = await db.scalar(
        select(BoardColumn)
        .join(Project, Project.id == BoardColumn.project_id)
        .where(BoardColumn.id == column_id, Project.owner_id == user_id)
    )
    if column is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Колонка не найдена")
    return column


@router.get("/columns", response_model=list[BoardColumnResponse])
async def list_columns(principal: CurrentPrincipal, db: Db, project_id: int = Query(gt=0)):
    """Только чтение.

    Раньше здесь создавались колонки по умолчанию, и два параллельных запроса
    к пустой доске делали два набора. Набор создаётся при создании проекта,
    а старым проектам он добавлен миграцией.
    """
    await _owned_project(db, project_id, principal.user_id)
    columns = (
        await db.scalars(
            select(BoardColumn)
            .where(BoardColumn.project_id == project_id)
            .order_by(BoardColumn.position, BoardColumn.id)
        )
    ).all()
    return list(columns)


@router.post("/columns", response_model=BoardColumnResponse, status_code=status.HTTP_201_CREATED)
async def create_column(payload: BoardColumnCreate, principal: CurrentPrincipal, db: Db):
    await _owned_project(db, payload.project_id, principal.user_id)
    # Позиция считается по максимуму существующих: без блокировки два
    # одновременных создания получили бы одно значение.
    await lock_project_board(db, payload.project_id)
    last = await db.scalar(
        select(func.max(BoardColumn.position)).where(BoardColumn.project_id == payload.project_id)
    )
    column = BoardColumn(
        project_id=payload.project_id,
        title=payload.title,
        is_done_column=payload.is_done_column,
        position=(last or 0.0) + ordering.STEP,
    )
    db.add(column)
    await db.flush()
    result = BoardColumnResponse.model_validate(column)
    await db.commit()
    return result


@router.patch("/columns/{column_id}", response_model=BoardColumnResponse)
async def update_column(
    column_id: int, payload: BoardColumnUpdate, principal: CurrentPrincipal, db: Db
):
    """Переименование и перемещение колонки между соседями."""
    column = await _owned_column(db, column_id, principal.user_id)
    fields = payload.model_dump(exclude_unset=True)

    if fields.get("title") is not None:
        column.title = fields["title"]
    if fields.get("is_done_column") is not None:
        column.is_done_column = fields["is_done_column"]

    if "before_id" in fields or "after_id" in fields:
        # Соседи читаются и позиция пишется несколькими запросами: без общей
        # блокировки два перемещения выбрали бы одно значение позиции.
        await lock_project_board(db, column.project_id)
        check_neighbours(column_id, fields.get("before_id"), fields.get("after_id"))

        async def neighbour_position(neighbour_id: int | None) -> float | None:
            if neighbour_id is None:
                return None
            position = await db.scalar(
                select(BoardColumn.position).where(
                    BoardColumn.id == neighbour_id,
                    BoardColumn.project_id == column.project_id,
                )
            )
            if position is None:
                raise field_error(["before_id"], "Соседняя колонка не найдена")
            return position

        previous = await neighbour_position(fields.get("after_id"))
        following = await neighbour_position(fields.get("before_id"))
        check_neighbour_order(previous, following)
        position = ordering.position_between(previous, following)
        if position is None:
            siblings = (
                await db.scalars(
                    select(BoardColumn)
                    .where(BoardColumn.project_id == column.project_id)
                    .order_by(BoardColumn.position, BoardColumn.id)
                    .with_for_update()
                )
            ).all()
            for slot, sibling in zip(ordering.renumber(len(siblings)), siblings, strict=True):
                sibling.position = slot
            await db.flush()
            previous = await neighbour_position(fields.get("after_id"))
            following = await neighbour_position(fields.get("before_id"))
            position = ordering.position_between(previous, following) or ordering.STEP
        column.position = position

    await db.flush()
    result = BoardColumnResponse.model_validate(column)
    await db.commit()
    return result


@router.delete("/columns/{column_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_column(column_id: int, principal: CurrentPrincipal, db: Db):
    """Задачи не удаляются вместе с колонкой: column_id обнуляется (SET NULL)."""
    column = await _owned_column(db, column_id, principal.user_id)
    # Проверка «остаётся хотя бы одна колонка» и само удаление — два запроса.
    # Без общей блокировки два параллельных DELETE проходят проверку каждый
    # по своему снимку и удаляют обе оставшиеся колонки.
    await lock_project_board(db, column.project_id)
    remaining = await db.scalar(
        select(func.count())
        .select_from(BoardColumn)
        .where(BoardColumn.project_id == column.project_id)
    )
    if remaining <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "Нельзя удалить последнюю колонку")

    # Задачи остаются: внешний ключ объявлен ON DELETE SET NULL, они просто
    # выпадают из колонок и показываются в «без колонки».
    await db.execute(sql_delete(BoardColumn).where(BoardColumn.id == column_id))
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
