"""Данные пользователя: экспорт, смена пароля, удаление аккаунта."""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from .. import audit
from ..cookies import clear_refresh_cookie
from ..dependencies import CurrentUser, Db, SettingsDep
from ..models import Attachment, Project, Task, TaskAttributeMeta, User
from ..schemas import ChangePasswordRequest, CurrentUserResponse, DeleteAccountRequest
from ..security import hash_password, require_csrf_guard, verify_password
from ..sessions import revoke_user_sessions
from ..storage import remove_attachments

router = APIRouter(prefix="/user", tags=["user"])


@router.get("/me", response_model=CurrentUserResponse)
async def read_current_user(response: Response, user: CurrentUser):
    """Кто вошёл: клиенту нужен id для ключей кэша и разделения аккаунтов."""
    response.headers["Cache-Control"] = "no-store"
    return user


@router.get("/export-data")
async def export_user_data(
    response: Response,
    user: CurrentUser,
    db: Db,
    settings: SettingsDep,
):
    """Синхронный экспорт небольшого объёма.

    Связь `Project.tasks` загружается явно через selectinload: при lazy="raise"
    обращение к `p.tasks` иначе подняло бы исключение, а не скрытый SQL.
    Хеши паролей и действующие токены в экспорт не попадают.
    """
    total_tasks = await db.scalar(
        select(func.count())
        .select_from(Task)
        .join(Project, Project.id == Task.project_id)
        .where(Project.owner_id == user.id)
    )
    if total_tasks and total_tasks > settings.export_max_tasks:
        # Показанный .all() не является вариантом для неограниченного набора:
        # выше порога требуется фоновая выгрузка порциями.
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"Экспорт содержит {total_tasks} задач(и) при пороге "
            f"{settings.export_max_tasks}; требуется фоновая выгрузка",
        )

    projects = (
        await db.scalars(
            select(Project)
            .where(Project.owner_id == user.id)
            .options(selectinload(Project.tasks))
            .order_by(Project.id)
        )
    ).all()
    metadata = (await db.scalars(select(TaskAttributeMeta).order_by(TaskAttributeMeta.code))).all()

    response.headers["Cache-Control"] = "no-store"
    return {
        "schema_version": 1,
        "user": {"id": user.id, "email": user.email, "created_at": user.created_at},
        "attribute_meta": [
            {"code": m.code, "title": m.title, "type": m.type, "is_required": m.is_required}
            for m in metadata
        ],
        "projects": [
            {
                "id": p.id,
                "title": p.title,
                "description": p.description,
                "created_at": p.created_at,
                "tasks": [
                    {
                        "id": t.id,
                        "attributes": t.attributes,
                        "created_at": t.created_at,
                        "updated_at": t.updated_at,
                    }
                    for t in sorted(p.tasks, key=lambda task: task.id)
                ],
            }
            for p in projects
        ],
    }


@router.post(
    "/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf_guard)],
)
async def change_password(
    payload: ChangePasswordRequest,
    response: Response,
    user: CurrentUser,
    db: Db,
    settings: SettingsDep,
):
    """Смена пароля отзывает все сессии: оставшиеся access-токены и открытые
    SSE-потоки перестают давать доступ, требуется повторный вход."""
    if not await verify_password(user.hashed_password, payload.current_password):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Неверный текущий пароль")
    if len(payload.new_password) < settings.password_min_length:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Пароль короче {settings.password_min_length} символов",
        )

    user.hashed_password = await hash_password(payload.new_password)
    db.add(user)
    await revoke_user_sessions(db, user.id, "password_changed")
    audit.add_audit(db, audit.PASSWORD_CHANGED, user_id=user.id)
    await db.commit()

    clear_refresh_cookie(response, settings)


@router.delete(
    "/me",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf_guard)],
)
async def delete_account(
    payload: DeleteAccountRequest,
    response: Response,
    user: CurrentUser,
    db: Db,
    settings: SettingsDep,
):
    """Удаление аккаунта: подтверждение пароля, отзыв сессий, каскадное удаление.

    Обработку резервных копий и журналов нужно согласовать с политикой хранения:
    один DELETE из `users` не реализует весь процесс.
    """
    if not await verify_password(user.hashed_password, payload.password):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Неверный пароль")

    user_id = user.id
    # Каскад убирает строки attachments, но не байты на диске: имена нужно
    # забрать до удаления, иначе файлы остаются навсегда и найти их будет
    # нечем — владельца у них больше нет.
    stored_names = list(
        await db.scalars(select(Attachment.stored_name).where(Attachment.owner_id == user_id))
    )
    await revoke_user_sessions(db, user_id, "account_deleted")
    audit.add_audit(db, audit.ACCOUNT_DELETED, user_id=user_id, detail=f"user_id={user_id}")
    await db.flush()
    # Проекты и задачи удаляются каскадом по внешним ключам.
    await db.execute(sql_delete(User).where(User.id == user_id))
    await db.commit()

    # Только после commit: удалить файлы у неудалённого аккаунта хуже, чем
    # оставить их у удалённого. Ошибка уборки не отменяет удаление аккаунта.
    await remove_attachments(settings, stored_names)

    clear_refresh_cookie(response, settings)
