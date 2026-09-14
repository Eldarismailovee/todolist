"""Ссылки на вложения внутри документа задачи.

Подписанных ссылок больше нет. Они существовали потому, что тег `<img>` не мог
отправить заголовок Authorization, а access token был одноразовым: приходилось
выдавать ссылку с HMAC и часовым сроком, то есть предъявительский доступ к файлу
без всякой сессии. Сессионная cookie покрывает весь `/api/v1`, поэтому картинка
запрашивается той же авторизацией, что и остальные данные, а выдача проверяет
владельца по самой сессии.

В документе хранится «голый» путь `/api/v1/files/{id}`; строка приводится к нему
и при записи, чтобы в JSONB не оседали чужие query-параметры.
"""

import re
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Attachment

FILE_PATH = re.compile(r"^/api/v1/files/([0-9a-fA-F-]{36})(?:\?.*)?$")


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
    """Путь без параметров.

    UUID приводится к каноничной форме: `download_file` разбирает идентификатор
    сам, и хранить в документе две записи одного и того же файла незачем.
    """
    try:
        return f"/api/v1/files/{UUID(raw_id)}"
    except ValueError:
        return f"/api/v1/files/{raw_id}"


def normalise_paths(content: Any) -> Any:
    """Перед сохранением: ссылка на вложение приводится к «голому» пути."""

    def transform(src: str) -> str:
        match = FILE_PATH.match(src)
        return _bare_path(match.group(1)) if match else src

    return _map_urls(content, transform) if content is not None else None


def _referenced(content: Any) -> set[UUID]:
    """Вложения, на которые ссылается документ."""
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


async def check_attachments(db: AsyncSession, owner_id: int, content: Any) -> None:
    """Перед записью: чужое вложение в документе — отказ.

    Выдача файла и так проверяет владельца, поэтому ссылка на чужой файл просто
    не открылась бы. Отказ на записи оставляет ошибку там, где её видно
    пользователю, вместо молча неработающей картинки в сохранённой задаче.
    """
    referenced = _referenced(content)
    if await _owned(db, owner_id, referenced) != referenced:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Вложение не найдено")
