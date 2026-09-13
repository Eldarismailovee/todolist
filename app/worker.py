"""Фоновая рассылка напоминаний о дедлайнах.

    uv run python -m app.worker

Воркер отдельный: рассылка не должна зависеть от того, обслуживает ли
веб-процесс запросы. Отметка об отправке уникальна по (task_id, kind), поэтому
одно и то же напоминание не уходит дважды — даже если воркеров несколько.
"""

import asyncio
import logging
import signal
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert

from .config import get_settings
from .db import SessionLocal, engine
from .integrations.mail import Mailer, create_mailer
from .integrations.telegram import TelegramSender, create_telegram_sender
from .logging_config import configure_logging
from .models import NotificationPrefs, Project, Task, TaskNotification, User
from .sessions import purge_expired_sessions

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


@dataclass(slots=True)
class _Reminder:
    """Всё нужное для отправки, снятое до фиксации заявки.

    После commit ORM-объекты протухают, и обращение к `task.title` стоило бы
    отдельного запроса на каждое напоминание.
    """

    task_id: int
    kind: str
    subject: str
    body: str
    email: str | None
    chat_id: str | None

    @property
    def planned_channels(self) -> list[str]:
        return [
            name for name, target in (("email", self.email), ("telegram", self.chat_id)) if target
        ]


async def _deliver(mailer: Mailer, telegram: TelegramSender, reminder: _Reminder) -> list[str]:
    """Отправляет одно напоминание. Возвращает каналы, которые сработали.

    В базу ничего не пишет: отметка об отправке занята до вызова, иначе два
    воркера отправили бы одно и то же напоминание одновременно.
    """
    channels: list[str] = []

    if reminder.email:
        try:
            await mailer.send(reminder.email, reminder.subject, reminder.body)
            channels.append("email")
        except Exception as error:  # noqa: BLE001 — канал не должен ронять воркер
            logger.warning("Письмо о задаче %s не отправлено: %s", reminder.task_id, error)

    if reminder.chat_id:
        # Заголовок задачи пишет пользователь, а сообщение уходит с
        # parse_mode=HTML: без экранирования «<» ломает разметку, и Telegram
        # отвергает сообщение целиком.
        message = f"<b>{escape(reminder.subject)}</b>\n{escape(reminder.body)}"
        if await telegram.send(reminder.chat_id, message):
            channels.append("telegram")

    return channels


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

        reminders: dict[tuple[int, str], _Reminder] = {}

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

            subject, body = _message(task, kind)
            reminders[(task.id, kind)] = _Reminder(
                task_id=task.id,
                kind=kind,
                subject=subject,
                body=body,
                email=user.email if prefs.email_enabled else None,
                chat_id=prefs.telegram_chat_id if prefs.telegram_enabled else None,
            )

        if not reminders:
            return 0

        # ON CONFLICT — расширение PostgreSQL: метода нет у общего
        # sqlalchemy.insert, поэтому конструктор берётся из диалекта.
        # В channels пока намерение; фактические каналы известны после отправки.
        claimed = (
            await db.execute(
                insert(TaskNotification)
                .values(
                    [
                        {
                            "task_id": reminder.task_id,
                            "kind": reminder.kind,
                            "channels": ",".join(reminder.planned_channels),
                        }
                        for reminder in reminders.values()
                    ]
                )
                .on_conflict_do_nothing(index_elements=["task_id", "kind"])
                .returning(TaskNotification.task_id, TaskNotification.kind)
            )
        ).all()

        # Заявка фиксируется до отправки: воркер, идущий параллельно, получит
        # из RETURNING пустоту и не пошлёт то же самое второй раз.
        await db.commit()

        for task_id, kind in claimed:
            reminder = reminders[(task_id, kind)]
            channels = await _deliver(mailer, telegram, reminder)
            row = (TaskNotification.task_id == task_id, TaskNotification.kind == kind)
            if channels:
                # Фактические каналы: почта могла не уйти, а Telegram — уйти.
                await db.execute(
                    update(TaskNotification).where(*row).values(channels=",".join(channels))
                )
                sent += 1
            else:
                # Ни один канал не сработал: заявка снимается, напоминание
                # уйдёт на следующем проходе.
                await db.execute(delete(TaskNotification).where(*row))
            await db.commit()

    return sent


async def main() -> None:
    configure_logging()
    settings = get_settings()
    stopping = asyncio.Event()
    # Уборка идёт в том же процессе, но реже рассылки: она не срочная.
    next_cleanup = 0.0

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

            if asyncio.get_running_loop().time() >= next_cleanup:
                next_cleanup = (
                    asyncio.get_running_loop().time() + settings.session_cleanup_interval_seconds
                )
                try:
                    removed = await purge_expired_sessions(settings)
                    # Число в журнале — единственная метрика роста таблицы,
                    # которая здесь есть: постоянно большое значение означает,
                    # что срок хранения или частота входов выбраны неверно.
                    logger.info("Удалено истёкших сессий: %s", removed)
                except Exception:  # noqa: BLE001 — уборка не должна ронять рассылку
                    logger.exception("Уборка сессий завершилась ошибкой")

            try:
                await asyncio.wait_for(stopping.wait(), timeout=settings.notification_poll_seconds)
            except TimeoutError:
                pass
    finally:
        await engine.dispose()
        logger.info("Воркер остановлен")


if __name__ == "__main__":
    asyncio.run(main())
