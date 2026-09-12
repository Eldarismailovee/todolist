"""Жизненный цикл байтов вложения: запись, сироты, удаление вместе с аккаунтом."""

import io
from pathlib import Path

import anyio
import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Attachment

from .conftest import PNG, bearer, fresh_access, register

settings = get_settings()
PASSWORD = "correct-horse-battery"


def _stored_path(name: str) -> Path:
    return Path(settings.upload_dir) / name


def _files_on_disk() -> set[Path]:
    directory = Path(settings.upload_dir)
    return set(directory.glob("*")) if directory.is_dir() else set()


async def _upload(client, name: str = "dot.png"):
    return await client.post(
        "/api/v1/files",
        files={"file": (name, io.BytesIO(PNG), "image/png")},
        headers=bearer(await fresh_access(client)),
    )


async def test_account_deletion_removes_attachment_bytes(client, db_session):
    """Каскад убирает строки attachments; байты должен убрать обработчик."""
    await register(client, "delete-files@example.com", PASSWORD)
    assert (await _upload(client)).status_code == 201

    stored_name = await db_session.scalar(select(Attachment.stored_name))
    assert stored_name is not None
    assert _stored_path(stored_name).is_file()

    deleted = await client.request(
        "DELETE",
        "/api/v1/user/me",
        json={"password": PASSWORD},
        headers=bearer(await fresh_access(client)),
    )

    assert deleted.status_code == 204, deleted.text
    assert not _stored_path(stored_name).exists()


async def test_failed_commit_does_not_leave_orphan_bytes(client, monkeypatch, db_session):
    """Запись байтов без сохранённой записи — потерянный навсегда файл."""
    await register(client, "orphan@example.com", PASSWORD)
    # Токен берётся до подмены: обмен refresh тоже делает commit.
    token = await fresh_access(client)

    before = await anyio.to_thread.run_sync(_files_on_disk)

    async def failing_commit(self: AsyncSession) -> None:
        raise OperationalError("commit", None, Exception("соединение потеряно"))

    monkeypatch.setattr(AsyncSession, "commit", failing_commit)

    with pytest.raises(OperationalError):
        await client.post(
            "/api/v1/files",
            files={"file": ("dot.png", io.BytesIO(PNG), "image/png")},
            headers=bearer(token),
        )

    monkeypatch.undo()
    after = await anyio.to_thread.run_sync(_files_on_disk)
    assert after == before
    assert await db_session.scalar(select(Attachment.id)) is None
