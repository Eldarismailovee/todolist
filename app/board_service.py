"""Общие правила доски: блокировка порядка, колонки по умолчанию, признак «готово».

Порядок задач и колонок — это состояние, которое читают, считают и записывают
несколькими запросами. Между чтением соседей и записью новой позиции успевает
вклиниться другой запрос, и два перемещения выбирают одно значение. Поэтому все
операции порядка внутри проекта сериализуются рекомендательной блокировкой
транзакции; она снимается сама на commit или rollback.
"""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import ordering
from .errors import field_error
from .models import BoardColumn, Task

# Пространство ключей: рекомендательные блокировки PostgreSQL глобальны для
# всей базы, и без своего namespace они столкнулись бы с чужими.
BOARD_LOCK_NAMESPACE = 4711

DEFAULT_COLUMNS = (("К выполнению", False), ("В работе", False), ("Готово", True))


async def lock_project_board(db: AsyncSession, project_id: int) -> None:
    """Сериализовать операции порядка и удаления колонок внутри проекта.

    Блокировка на уровне транзакции, а не строк: перенумерация, вставка между
    соседями и проверка «нельзя удалить последнюю колонку» затрагивают разные
    наборы строк, и общий для них замок — сам проект.
    """
    await db.execute(select(func.pg_advisory_xact_lock(BOARD_LOCK_NAMESPACE, project_id)))


async def create_default_columns(db: AsyncSession, project_id: int) -> list[BoardColumn]:
    """Колонки нового проекта. Вызывается при создании, а не при чтении доски.

    Создание из GET означало, что два параллельных чтения пустой доски могут
    сделать два набора колонок; чтение вообще не должно писать.
    """
    created = []
    for index, (title, is_done) in enumerate(DEFAULT_COLUMNS):
        column = BoardColumn(
            project_id=project_id,
            title=title,
            position=ordering.STEP * (index + 1),
            is_done_column=is_done,
        )
        db.add(column)
        created.append(column)
    await db.flush()
    return created


def check_neighbours(moved_id: int, before_id: int | None, after_id: int | None) -> None:
    """Соседи задают место вставки, поэтому противоречивые пары отвергаются.

    Сам перемещаемый элемент своим соседом быть не может, а before и after не
    могут совпадать: в обоих случаях «место между соседями» не определено, и
    раньше такой запрос молча получал произвольную позицию.
    """
    if moved_id in (before_id, after_id):
        raise field_error(["before_id"], "Элемент не может быть соседом самому себе")
    if before_id is not None and before_id == after_id:
        raise field_error(["before_id"], "before_id и after_id указывают на один элемент")


def check_neighbour_order(previous: float | None, following: float | None) -> None:
    """after_id должен идти в списке раньше before_id."""
    if previous is not None and following is not None and previous >= following:
        raise field_error(["before_id"], "Соседи указаны в неверном порядке")


def apply_column_change(task: Task, column: BoardColumn | None) -> None:
    """Единственное правило связи колонки и признака выполнения.

    Одно и то же пользовательское действие через PATCH и через move давало
    разные состояния: PATCH выставлял completed_at при переносе в колонку
    «готово», но не снимал его при переносе обратно, а move при column_id=null
    сохранял прежнее значение. Правило одно: задача считается выполненной,
    пока она лежит в колонке «готово»; любая другая колонка и «без колонки»
    открывают её снова.
    """
    task.column_id = column.id if column is not None else None
    if column is not None and column.is_done_column:
        # Момент закрытия сохраняется: повторное сохранение не «переоткрывает»
        # задачу и не сдвигает дату выполнения.
        task.completed_at = task.completed_at or datetime.now(UTC)
    else:
        task.completed_at = None
