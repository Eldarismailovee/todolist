"""Загрузка серверных метаданных и приведение ошибок валидации к 422."""

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import TaskAttributeMeta
from .schemas import MetaField, validate_attributes


async def load_metadata(db: AsyncSession) -> list[MetaField]:
    """Значения метаданных берутся только с сервера, не из тела запроса."""
    rows = (await db.scalars(select(TaskAttributeMeta).order_by(TaskAttributeMeta.code))).all()
    return [MetaField.model_validate(row) for row in rows]


def validate_or_422(raw: dict, metadata: list[MetaField]) -> dict:
    try:
        return validate_attributes(raw, metadata)
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=[
                {
                    "loc": ["body", "attributes", *(str(part) for part in error["loc"])],
                    "msg": error["msg"],
                    "type": error["type"],
                }
                for error in exc.errors(include_url=False, include_input=False)
            ],
        ) from exc
