"""Данные пользователя: экспорт, смена пароля, удаление аккаунта."""

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from .. import audit, otp
from ..cookies import clear_session_cookie
from ..dependencies import CurrentUser, Db, MailerDep, RedisDep, SettingsDep
from ..models import Attachment, Category, Project, Tag, Task, TaskAttributeMeta, User
from ..schemas import (
    ChangePasswordRequest,
    CurrentUserResponse,
    DeleteAccountRequest,
    DeleteCodeChallengeResponse,
)
from ..security import enforce_rate_limit, hash_password, verify_password
from ..sessions import revoke_user_sessions
from ..storage import remove_attachments

router = APIRouter(prefix="/user", tags=["user"])


async def _limit_password_attempts(redis, settings, user_id: int) -> None:
    """Свой счётчик попыток на пользователя для проверок пароля.

    Лимиты входа по IP этого не заменяют: они считают другие запросы, а перебор
    текущего пароля идёт из уже авторизованной сессии.
    """
    await enforce_rate_limit(
        redis,
        settings,
        f"password:{user_id}",
        settings.login_rate_limit,
        settings.login_rate_window_seconds,
    )


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

    Связи загружаются явно через selectinload: при lazy="raise" обращение к
    `p.tasks` иначе подняло бы исключение, а не скрытый SQL. Хеши паролей и
    действующие токены в экспорт не попадают.

    Выгрузка должна позволять восстановить список дел: раньше в неё попадали
    только id, атрибуты и отметки времени задачи, то есть ни заголовка, ни
    текста, ни срока в ней не было. Ссылки на вложения не подписываются —
    подпись живёт час, а выгрузка хранится долго; вместо неё идёт перечень
    файлов с постоянными идентификаторами.
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
            .options(
                # Теги грузятся вложенно: связь объявлена lazy="raise", и
                # обращение к t.tags в цикле иначе подняло бы исключение.
                selectinload(Project.tasks).selectinload(Task.tags),
                selectinload(Project.columns),
            )
            .order_by(Project.id)
        )
    ).all()
    metadata = (await db.scalars(select(TaskAttributeMeta).order_by(TaskAttributeMeta.code))).all()
    categories = (
        await db.scalars(select(Category).where(Category.owner_id == user.id).order_by(Category.id))
    ).all()
    tags = (await db.scalars(select(Tag).where(Tag.owner_id == user.id).order_by(Tag.id))).all()
    attachments = (
        await db.scalars(
            select(Attachment).where(Attachment.owner_id == user.id).order_by(Attachment.created_at)
        )
    ).all()

    response.headers["Cache-Control"] = "no-store"
    return {
        # Версия поднята: набор полей задачи изменился, и импорт обязан
        # отличать старую выгрузку от новой.
        "schema_version": 2,
        "user": {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name,
            "has_password": user.has_password,
            "created_at": user.created_at,
        },
        "attribute_meta": [
            {"code": m.code, "title": m.title, "type": m.type, "is_required": m.is_required}
            for m in metadata
        ],
        "categories": [{"id": c.id, "name": c.name, "color": c.color} for c in categories],
        "tags": [{"id": t.id, "name": t.name, "color": t.color} for t in tags],
        # Байты в JSON не вкладываются: перечень даёт постоянный идентификатор,
        # по которому файл выдаётся владельцу отдельным запросом.
        "attachments": [
            {
                "id": str(a.id),
                "filename": a.filename,
                "content_type": a.content_type,
                "size_bytes": a.size_bytes,
                "created_at": a.created_at,
            }
            for a in attachments
        ],
        "projects": [
            {
                "id": p.id,
                "title": p.title,
                "description": p.description,
                "created_at": p.created_at,
                "columns": [
                    {
                        "id": c.id,
                        "title": c.title,
                        "position": c.position,
                        "is_done_column": c.is_done_column,
                    }
                    for c in sorted(p.columns, key=lambda column: column.position)
                ],
                "tasks": [
                    {
                        "id": t.id,
                        "title": t.title,
                        "description": t.description,
                        # Документ хранится с «голыми» путями: подписанная
                        # ссылка через час перестала бы работать.
                        "content": t.content,
                        "column_id": t.column_id,
                        "category_id": t.category_id,
                        "position": t.position,
                        "due_at": t.due_at,
                        "completed_at": t.completed_at,
                        "tag_ids": sorted(tag.id for tag in t.tags),
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


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: ChangePasswordRequest,
    response: Response,
    user: CurrentUser,
    db: Db,
    redis: RedisDep,
    settings: SettingsDep,
):
    """Смена пароля отзывает все сессии: cookie в других браузерах и открытые
    SSE-потоки перестают давать доступ, требуется повторный вход."""
    await _limit_password_attempts(redis, settings, user.id)
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

    clear_session_cookie(response, settings)


@router.post(
    "/delete-code",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=DeleteCodeChallengeResponse,
)
async def request_delete_code(
    request: Request,
    user: CurrentUser,
    db: Db,
    redis: RedisDep,
    settings: SettingsDep,
    mailer: MailerDep,
):
    """Код подтверждения удаления аккаунта на адрес владельца.

    Нужен аккаунтам без пароля: у входа через OAuth подтверждать нечего, а
    удаление данных обязано требовать свежего подтверждения личности. Код
    привязан к действию: цель `delete_account` не принимается на
    /auth/otp/verify и сессию не создаёт.
    """
    await enforce_rate_limit(
        redis,
        settings,
        f"otp:delete:{user.id}",
        settings.otp_request_limit,
        settings.otp_request_window_seconds,
    )
    await otp.send_code(db, settings, mailer, redis, user.email, "delete_account")
    return {
        "otp_required": True,
        # Цель в ответе — та же, что у кода: клиент не должен её угадывать.
        "purpose": "delete_account",
        "expires_in": settings.otp_ttl_seconds,
    }


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    payload: DeleteAccountRequest,
    response: Response,
    user: CurrentUser,
    db: Db,
    redis: RedisDep,
    settings: SettingsDep,
):
    """Удаление аккаунта: подтверждение личности, отзыв сессий, каскад.

    Обработку резервных копий и журналов нужно согласовать с политикой хранения:
    один DELETE из `users` не реализует весь процесс.
    """
    # Число попыток подтверждения ограничено на пользователя: и пароль, и код
    # здесь — секрет, который можно перебирать из действующей сессии.
    await _limit_password_attempts(redis, settings, user.id)
    if payload.password is not None:
        # Аккаунту без пароля пароль подтверждением быть не может: сравнение с
        # фиктивным хешем всё равно вернёт отказ, и это правильный ответ.
        if not await verify_password(user.hashed_password, payload.password):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Неверный пароль")
    else:
        try:
            await otp.consume_code(db, settings, user.email, payload.code, "delete_account")
        except otp.OtpError as error:
            # Попытка засчитана и зафиксирована до отказа, иначе перебор
            # шестизначного кода не оставлял бы следов.
            await db.commit()
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Неверный или истёкший код") from error

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

    clear_session_cookie(response, settings)
