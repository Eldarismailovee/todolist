"""Файловое хранилище вложений.

`Path.mkdir`, `Path.write_bytes` и `Path.is_file` — блокирующие вызовы. В
async-обработчике они останавливают весь событийный цикл на время обращения к
диску: `async def` сам по себе не делает их асинхронными. Поэтому каждая
дисковая операция уходит в рабочий поток.

Хранилище локальное. Реплики API с разными дисками не увидят вложений друг
друга, а база и файловая система не образуют общей транзакции. Полное решение —
общий объектный store и явный жизненный цикл вложения (staged → ready →
deleting) с повторяемой уборкой сирот фоновым заданием; здесь его нет.
"""

import logging
from pathlib import Path

import anyio

from .config import Settings

logger = logging.getLogger(__name__)


def storage_dir(settings: Settings) -> Path:
    """Каталог хранения. Чтение каталог не создаёт: это дело записи."""
    return Path(settings.upload_dir)


async def write_attachment(settings: Settings, stored_name: str, data: bytes) -> None:
    target = storage_dir(settings) / stored_name

    def write() -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    await anyio.to_thread.run_sync(write)


async def find_attachment(settings: Settings, stored_name: str) -> Path | None:
    """Путь к существующему файлу или None, если байтов на диске больше нет."""
    path = storage_dir(settings) / stored_name
    exists = await anyio.to_thread.run_sync(path.is_file)
    return path if exists else None


async def remove_attachments(settings: Settings, stored_names: list[str]) -> None:
    """Удалить байты. Один поток на весь список: файлов может быть много.

    Отсутствующий файл — не ошибка: удаление обязано быть повторяемым.
    """
    if not stored_names:
        return
    directory = storage_dir(settings)

    def remove() -> list[str]:
        failed: list[str] = []
        for name in stored_names:
            try:
                (directory / name).unlink(missing_ok=True)
            except OSError:
                failed.append(name)
        return failed

    failed = await anyio.to_thread.run_sync(remove)
    if failed:
        # Записи в БД уже нет, поэтому сами файлы теперь может найти только
        # уборка по каталогу. Имена нужны в журнале, чтобы она была возможна.
        logger.warning("Не удалось удалить файлы вложений: %s", ", ".join(failed))
