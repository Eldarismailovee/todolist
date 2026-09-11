"""Фоновая рассылка напоминаний о дедлайнах.

    uv run python -m app.worker

Воркер отдельный: рассылка не должна зависеть от того, обслуживает ли
веб-процесс запросы. Отметка об отправке уникальна по (task_id, kind), поэтому
одно и то же напоминание не уходит дважды — даже если воркеров несколько.
"""

import asyncio
import logging
import signal
from datetime import UTC, datetime, timedelta

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import SessionLocal, engine
from .integrations.mail import Mailer, create_mailer
from .integrations.telegram import TelegramSender, create_telegram_sender
from .models import NotificationPrefs, Project, Task, TaskNotification, User

logger = logging.getLogger(__name__)

DUE_SOON = "due_soon"
OVERDUE = "overdue"


def _message(task: Task, kind: str) -> tuple[str, str]:
    when = task.due_at.strftime("%d.%m.%Y %H:%M") if task.due_at else "—"
    if kind == OVERDUE:
        subject = f"Просрочено: {task.title}"
        body = f"Задача «{task.title}» просрочена. Срок был {when}."
    else:
        subject = f"Скоро дедлайн: {task.title}"
        body = f"Задача «{task.title}» должна быть выполнена к {when}."
    return subject, body


async def _deliver(
    db: AsyncSession,
    mailer: Mailer,
    telegram: TelegramSender,
    task: Task,
    user: User,
    prefs: NotificationPrefs,
    kind: str,
) -> bool:
    """Отправляет одно напоминание. False — если канал не сработал."""
    subject, body = _message(task, kind)
    channels: list[str] = []

    if prefs.email_enabled:
        try:
            await mailer.send(user.email, subject, body)
            channels.append("email")
        except Exception as error:  # noqa: BLE001 — канал не должен ронять воркер
            logger.warning("Письмо о задаче %s не отправлено: %s", task.id, error)

    if prefs.telegram_enabled and prefs.telegram_chat_id:
        if await telegram.send(prefs.telegram_chat_id, f"<b>{subject}</b>\n{body}"):
            channels.append("telegram")

    if not channels:
        return False

    db.add(TaskNotification(task_id=task.id, kind=kind, channels=",".join(channels)))
    try:
        await db.commit()
    except IntegrityError:
        # Другой воркер успел раньше: дубликат не создаётся.
        await db.rollback()
    return True


async def run_once() -> int:
    """Один проход. Возвращает число отправленных напоминаний."""
    settings = get_settings()
    mailer = create_mailer(settings)
    telegram = create_telegram_sender(settings)
    now = datetime.now(UTC)
    sent = 0

    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(Task, User, NotificationPrefs)
                .join(Project, Project.id == Task.project_id)
                .join(User, User.id == Project.owner_id)
                .outerjoin(NotificationPrefs, NotificationPrefs.user_id == User.id)
                .where(
                    Task.due_at.is_not(None),
                    Task.completed_at.is_(None),
                    User.is_active.is_(True),
                    # Далёкие сроки не трогаем: выборка должна оставаться узкой.
                    Task.due_at <= now + timedelta(days=7),
                )
                .order_by(Task.due_at)
                .limit(settings.notification_batch_size)
            )
        ).all()

        notifications_to_insert = []
        tasks_mapping = {}

        for task, user, prefs in rows:
            # Значения по умолчанию проставляются при INSERT, поэтому у
            # несохранённого объекта поля были бы None и почта молча
            # отключилась бы для всех, кто не открывал настройки.
            prefs = prefs or NotificationPrefs(
                user_id=user.id,
                email_enabled=True,
                telegram_enabled=False,
                lead_time_minutes=60,
            )
            if not prefs.email_enabled and not prefs.telegram_enabled:
                continue

            lead = timedelta(minutes=prefs.lead_time_minutes)
            if task.due_at < now:
                kind = OVERDUE
            elif task.due_at <= now + lead:
                kind = DUE_SOON
            else:
                continue

            # Собираем каналы динамически на основе настроек пользователя
            channels_list = []
            if prefs.email_enabled:
                channels_list.append("email")
            if prefs.telegram_enabled:
                channels_list.append("telegram")
            channels_str = ",".join(channels_list)

            # Добавляем в список для массовой вставки
            notifications_to_insert.append({
                "task_id": task.id,
                "kind": kind,
                "channels": channels_str,
            })
            
            # Запоминаем контекст для последующей отправки
            tasks_mapping[(task.id, kind)] = (task, user, prefs)

        if notifications_to_insert:
            stmt = (
                insert(TaskNotification)
                .values(notifications_to_insert)
                .on_conflict_do_nothing(index_elements=["task_id", "kind"])
                .returning(TaskNotification.task_id, TaskNotification.kind)
            )
            
            result = await db.execute(stmt)
            inserted_rows = result.all()  # Получаем только те записи, которые реально создались

            # Если внутри _deliver() будут делаться изменения в этой же сессии db,
            # полезно зафиксировать стейт вставки, чтобы избежать конфликтов блокировок
            await db.flush()

            # Отправляем уведомления только для успешно вставленных записей
            for task_id, kind in inserted_rows:
                task, user, prefs = tasks_mapping[(task_id, kind)]
                
                if await _deliver(db, mailer, telegram, task, user, prefs, kind):
                    sent += 1
                    
        # Фиксируем транзакцию в конце прохода
        await db.commit()

    return sent


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    stopping = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopping.set)

    logger.info("Воркер уведомлений запущен, интервал %s с", settings.notification_poll_seconds)
    try:
        while not stopping.is_set():
            try:
                sent = await run_once()
                if sent:
                    logger.info("Отправлено напоминаний: %s", sent)
            except Exception:  # noqa: BLE001 — цикл переживает единичный сбой
                logger.exception("Проход воркера завершился ошибкой")
            try:
                await asyncio.wait_for(stopping.wait(), timeout=settings.notification_poll_seconds)
            except TimeoutError:
                pass
    finally:
        await engine.dispose()
        logger.info("Воркер остановлен")


if __name__ == "__main__":
    asyncio.run(main())
