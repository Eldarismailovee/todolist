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


@pytest.mark.parametrize(
    ("content_type", "payload", "expected"),
    [
        ("image/png", PNG, 201),
        ("image/gif", b"GIF89a" + b"\x00" * 32, 201),
        ("image/jpeg", b"\xff\xd8\xff\xe0" + b"\x00" * 32, 201),
        ("image/webp", b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 16, 201),
        ("image/svg+xml", b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>', 201),
        # Заголовок клиента типом файла не является: под видом картинки
        # сохранялось бы произвольное содержимое, а отдавалось с image/*.
        ("image/png", b"<html><script>alert(1)</script></html>", 415),
        # MZ — начало исполняемого файла Windows, не SVG.
        ("image/svg+xml", b"MZ\x90\x00\x03\x00\x00\x00", 415),
        ("image/jpeg", PNG, 415),
    ],
)
async def test_upload_checks_the_actual_format(client, content_type, payload, expected):
    await register(client, f"format{abs(hash((content_type, expected)))}@example.com", PASSWORD)

    response = await client.post(
        "/api/v1/files",
        files={"file": ("file", io.BytesIO(payload), content_type)},
        headers=bearer(await fresh_access(client)),
    )

    assert response.status_code == expected, response.text


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
