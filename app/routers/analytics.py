"""Аналитика продуктивности для графиков."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query
from sqlalchemy import Date, case, cast, func, select

from ..dependencies import CurrentPrincipal, Db
from ..models import Category, Project, Task
from ..schemas import AnalyticsResponse

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/summary", response_model=AnalyticsResponse)
async def summary(
    principal: CurrentPrincipal,
    db: Db,
    days: int = Query(default=30, ge=1, le=365),
    project_id: int | None = Query(default=None, gt=0),
):
    now = datetime.now(UTC)
    # График группирует по календарным датам, поэтому и граница выборки — начало
    # дня, а не «столько же времени назад»: иначе первый столбец диапазона
    # молча терял всё, что произошло раньше текущего времени суток.
    # День считается в UTC; пользовательский часовой пояс сервер пока не знает,
    # и это должно быть видно из кода, а не подразумеваться.
    since = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    owned = Task.project_id.in_(select(Project.id).where(Project.owner_id == principal.user_id))
    scope = [owned] if project_id is None else [owned, Task.project_id == project_id]

    totals = (
        await db.execute(
            select(
                func.count(Task.id),
                func.count(Task.completed_at),
                func.count(
                    case(
                        (
                            (Task.due_at < now) & (Task.completed_at.is_(None)),
                            Task.id,
                        )
                    )
                ),
                func.count(
                    case(
                        (
                            (Task.due_at >= now)
                            & (Task.due_at <= now + timedelta(days=7))
                            & (Task.completed_at.is_(None)),
                            Task.id,
                        )
                    )
                ),
            ).where(*scope)
        )
    ).one()
    total, completed, overdue, due_soon = totals

    # Одна выборка по дням вместо запроса на каждую дату. Дата берётся явно в
    # UTC: приведение timestamptz к date использует настройку TimeZone сессии,
    # и без указания зоны столбцы графика зависели бы от конфигурации СУБД.
    created_day = cast(func.timezone("UTC", Task.created_at), Date)
    completed_day = cast(func.timezone("UTC", Task.completed_at), Date)
    created_rows = dict(
        (
            await db.execute(
                select(created_day, func.count(Task.id))
                .where(*scope, Task.created_at >= since)
                .group_by(created_day)
            )
        ).all()
    )
    completed_rows = dict(
        (
            await db.execute(
                select(completed_day, func.count(Task.id))
                .where(*scope, Task.completed_at.is_not(None), Task.completed_at >= since)
                .group_by(completed_day)
            )
        ).all()
    )

    daily = []
    for offset in range(days):
        day = (since + timedelta(days=offset)).date()
        daily.append(
            {
                "date": day.isoformat(),
                "created": created_rows.get(day, 0),
                "completed": completed_rows.get(day, 0),
            }
        )

    by_category = [
        {
            "name": name or "Без категории",
            "color": color or "#94a3b8",
            "total": int(category_total),
            "completed": int(category_done),
        }
        for name, color, category_total, category_done in (
            await db.execute(
                select(
                    Category.name,
                    Category.color,
                    func.count(Task.id),
                    func.count(Task.completed_at),
                )
                .select_from(Task)
                .outerjoin(Category, Category.id == Task.category_id)
                .where(*scope)
                .group_by(Category.name, Category.color)
                .order_by(func.count(Task.id).desc())
            )
        ).all()
    ]

    return {
        "range_days": days,
        "total": total,
        "completed": completed,
        "overdue": overdue,
        "due_soon": due_soon,
        "completion_rate": round(completed / total, 4) if total else 0.0,
        "daily": daily,
        "by_category": by_category,
    }
