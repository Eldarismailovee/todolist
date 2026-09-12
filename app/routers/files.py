"""Загрузка изображений для редактора и их выдача.

Файл кладётся на диск под случайным именем, в БД остаётся только запись с
владельцем. Скачивание разрешает либо подписанная ссылка (её ставит сервер в
содержимое задачи), либо обычный access token — тег `<img>` может использовать
только первый вариант.
"""

import logging
import secrets
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..attachments import sign_url, verify_signature
from ..config import Settings
from ..dependencies import CurrentPrincipal, Db, OptionalPrincipal, SettingsDep
from ..models import Attachment
from ..schemas import AttachmentResponse
from ..storage import find_attachment, remove_attachments, write_attachment

router = APIRouter(prefix="/files", tags=["files"])

logger = logging.getLogger(__name__)

# Расширение выбирает сервер по заявленному типу: имя из браузера в путь
# не попадает вовсе.
EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}

# SVG умеет выполнять скрипты при прямом открытии, поэтому у выдачи жёсткий
# CSP и запрет на угадывание типа.
SAFE_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "private, max-age=3600",
}


async def _discard_orphan_bytes(
    db: AsyncSession,
    settings: Settings,
    attachment_id: UUID,
    stored_name: str,
) -> None:
    """Снять байты, если запись о них не сохранилась.

    Commit мог как пройти, так и не пройти: сообщение о неудаче не означает,
    что транзакция откатилась. Удалять файл вслепую нельзя — он мог бы
    принадлежать уже созданной записи. Поэтому результат перечитывается, и
    файл удаляется только при доказанном отсутствии строки. Когда ответа нет,
    файл остаётся сиротой и его имя уходит в журнал: это задача уборки, а не
    обработчика запроса.
    """
    try:
        await db.rollback()
        exists = await db.scalar(select(Attachment.id).where(Attachment.id == attachment_id))
    except Exception:
        logger.warning("Файл вложения остался без подтверждённой записи: %s", stored_name)
        return
    if exists is None:
        await remove_attachments(settings, [stored_name])


@router.post("", response_model=AttachmentResponse, status_code=status.HTTP_201_CREATED)
async def upload_file(
    principal: CurrentPrincipal,
    db: Db,
    settings: SettingsDep,
    file: UploadFile = File(...),
):
    if file.content_type not in settings.allowed_upload_types:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"Тип {file.content_type} не поддерживается"
        )

    # Читаем порциями и обрываем на превышении: заявленный размер — не проверка.
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(64 * 1024):
        total += len(chunk)
        if total > settings.max_upload_bytes:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE,
                f"Файл больше {settings.max_upload_bytes // 1024 // 1024} МБ",
            )
        chunks.append(chunk)
    if total == 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Пустой файл")

    stored_name = f"{secrets.token_urlsafe(24)}{EXTENSIONS.get(file.content_type, '')}"

    attachment = Attachment(
        owner_id=principal.user_id,
        filename=(file.filename or "file")[:255],
        content_type=file.content_type,
        size_bytes=total,
        stored_name=stored_name,
    )
    db.add(attachment)
    # Строка создаётся до записи байтов: отказ БД тогда не оставляет файла.
    await db.flush()
    await write_attachment(settings, stored_name, b"".join(chunks))

    result = AttachmentResponse(
        id=str(attachment.id),
        # Клиент сразу получает подписанную ссылку и вставляет её в редактор.
        url=sign_url(settings, str(attachment.id)),
        filename=attachment.filename,
        content_type=attachment.content_type,
        size_bytes=attachment.size_bytes,
    )
    try:
        await db.commit()
    except Exception:
        await _discard_orphan_bytes(db, settings, attachment.id, stored_name)
        raise
    return result


@router.get("/{attachment_id}")
async def download_file(
    attachment_id: UUID,
    principal: OptionalPrincipal,
    db: Db,
    settings: SettingsDep,
    exp: str = Query(default=""),
    sig: str = Query(default=""),
):
    """Доступ даёт либо подписанная ссылка, либо access token владельца."""
    attachment = await db.scalar(select(Attachment).where(Attachment.id == attachment_id))
    if attachment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Файл не найден")

    signed = bool(sig) and verify_signature(settings, str(attachment_id), exp, sig)
    if not signed and (principal is None or attachment.owner_id != principal.user_id):
        # Для чужого файла ответ такой же, как для отсутствующего.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Файл не найден")

    path = await find_attachment(settings, attachment.stored_name)
    if path is None:
        raise HTTPException(status.HTTP_410_GONE, "Файл больше не доступен")

    media_type = attachment.content_type or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        headers=SAFE_HEADERS,
        # inline только для растровых картинок; SVG уходит вложением.
        content_disposition_type=(
            "inline"
            if media_type.startswith("image/") and media_type != "image/svg+xml"
            else "attachment"
        ),
        filename=attachment.filename,
    )
