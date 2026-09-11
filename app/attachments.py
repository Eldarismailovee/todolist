"""Подписанные ссылки на файлы.

Тег `<img>` не может отправить заголовок Authorization, а access token
одноразовый — по нему картинку не покажешь. Поэтому в документе хранится
«голый» путь `/api/v1/files/{id}`, а при чтении сервер подставляет ссылку с
подписью и коротким сроком жизни. Ссылка — доступ на время, а не навсегда.

Подпись выпускается только на вложение владельца документа: скачивание по
подписи идёт без сессии, поэтому подписать чужой файл — значит выдать права,
которых у владельца задачи нет. Ссылку в документ пишет клиент, так что
владельца проверяет и запись, и выдача.
"""

import base64
import hmac
import re
import time
from collections.abc import Iterable
from hashlib import sha256
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .models import Attachment

FILE_PATH = re.compile(r"^/api/v1/files/([0-9a-fA-F-]{36})(?:\?.*)?$")
SIGNED_URL_TTL = 3600


def _signature(settings: Settings, attachment_id: str, expires_at: int) -> str:
    message = f"{attachment_id}:{expires_at}".encode()
    digest = hmac.new(settings.secret_key.get_secret_value().encode(), message, sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def sign_url(settings: Settings, attachment_id: str, ttl: int = SIGNED_URL_TTL) -> str:
    expires_at = int(time.time()) + ttl
    signature = _signature(settings, attachment_id, expires_at)
    return f"/api/v1/files/{attachment_id}?exp={expires_at}&sig={signature}"


def verify_signature(settings: Settings, attachment_id: str, expires: str, signature: str) -> bool:
    if not expires.isdigit():
        return False
    expires_at = int(expires)
    if expires_at <= time.time():
        return False
    expected = _signature(settings, attachment_id, expires_at)
    return hmac.compare_digest(expected, signature)


def _map_urls(node: Any, transform) -> Any:
    """Обходит документ и заменяет src у изображений."""
    if isinstance(node, list):
        return [_map_urls(item, transform) for item in node]
    if not isinstance(node, dict):
        return node

    result = dict(node)
    attrs = result.get("attrs")
    if isinstance(attrs, dict) and isinstance(attrs.get("src"), str):
        attrs = dict(attrs)
        attrs["src"] = transform(attrs["src"])
        result["attrs"] = attrs
    if "content" in result:
        result["content"] = _map_urls(result["content"], transform)
    return result


def _bare_path(raw_id: str) -> str:
    """Путь без подписи.

    UUID приводится к каноничной форме: подпись считается по строке, а
    `download_file` разбирает идентификатор до проверки. Ссылка вида
    `/api/v1/files/AAAA...` иначе подписывалась бы не тем сообщением, каким
    проверяется, и не открывалась бы.
    """
    try:
        return f"/api/v1/files/{UUID(raw_id)}"
    except ValueError:
        return f"/api/v1/files/{raw_id}"


def strip_signatures(content: Any) -> Any:
    """Перед сохранением: подпись из документа убирается, остаётся путь."""

    def transform(src: str) -> str:
        match = FILE_PATH.match(src)
        return _bare_path(match.group(1)) if match else src

    return _map_urls(content, transform) if content is not None else None


def sign_content(settings: Settings, content: Any, owned: set[UUID]) -> Any:
    """Перед отдачей клиенту: путь превращается в подписанную ссылку.

    Подписывается только вложение из `owned`. Чужая ссылка (её мог оставить
    документ, сохранённый до проверки на записи) остаётся голым путём: картинка
    не откроется, а подпись не выдаст доступ.
    """

    def transform(src: str) -> str:
        match = FILE_PATH.match(src)
        if match is None:
            return src
        try:
            attachment_id = UUID(match.group(1))
        except ValueError:
            return _bare_path(match.group(1))
        if attachment_id not in owned:
            return _bare_path(match.group(1))
        return sign_url(settings, str(attachment_id))

    return _map_urls(content, transform) if content is not None else None


def _referenced(content: Any) -> set[UUID]:
    """Вложения, на которые ссылается документ.

    Обход — тот же `_map_urls`, что и у подписи: узел, до которого добирается
    `sign_content`, не может ускользнуть от проверки владельца.
    """
    found: set[UUID] = set()

    def collect(src: str) -> str:
        match = FILE_PATH.match(src)
        if match is not None:
            try:
                found.add(UUID(match.group(1)))
            except ValueError:
                # Не UUID — такой путь не откроется: download разбирает
                # идентификатор до всякой проверки доступа.
                pass
        return src

    _map_urls(content, collect)
    return found


async def _owned(db: AsyncSession, owner_id: int, referenced: set[UUID]) -> set[UUID]:
    if not referenced:
        return set()
    rows = await db.scalars(
        select(Attachment.id).where(Attachment.owner_id == owner_id, Attachment.id.in_(referenced))
    )
    return set(rows)


async def owned_attachments(db: AsyncSession, owner_id: int, contents: Iterable[Any]) -> set[UUID]:
    """Вложения владельца среди всех ссылок документов — одним запросом.

    Список задач подписывается пачкой: запрос на задачу превратил бы выдачу
    доски в десятки round-trip.
    """
    referenced: set[UUID] = set()
    for content in contents:
        referenced |= _referenced(content)
    return await _owned(db, owner_id, referenced)


async def check_attachments(db: AsyncSession, owner_id: int, content: Any) -> None:
    """Перед записью: чужое вложение в документе — отказ.

    Молчаливо сохранённая ссылка вернулась бы подписью при следующем чтении.
    """
    referenced = _referenced(content)
    if await _owned(db, owner_id, referenced) != referenced:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Вложение не найдено")
