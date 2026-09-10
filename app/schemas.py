"""Pydantic v2: строгая проверка тела запроса и динамических JSONB-атрибутов.

Схема атрибутов строится сервером по `TaskAttributeMeta`. Неизвестные ключи дают
422 и не исчезают молча при сохранении; `"false"` и `0` не принимаются вместо
boolean; дата принимается только как существующая календарная дата YYYY-MM-DD.
"""

import json
import re
from datetime import date, datetime
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    StrictBool,
    StrictStr,
    StringConstraints,
    create_model,
    field_validator,
)

from .config import get_settings

AttributeCode = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_]{0,49}$")]
PositiveId = Annotated[int, Field(strict=True, gt=0)]

# Имена, конфликтующие с публичным API BaseModel, нельзя использовать как код
# атрибута: create_model() перекрыл бы метод модели.
RESERVED_ATTRIBUTE_CODES = frozenset(
    name for name in dir(BaseModel) if not name.startswith("__")
) | {"model_config", "model_fields", "model_computed_fields"}


def parse_iso_date(value: Any) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("Ожидается дата в формате YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Несуществующая календарная дата") from exc


ISODate = Annotated[date, BeforeValidator(parse_iso_date)]


# --- Auth ----------------------------------------------------------------


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=512)]


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: Annotated[str, StringConstraints(strict=True, min_length=12, max_length=512)]


class RefreshRequest(BaseModel):
    """`user_id` клиент не передаёт: он берётся из серверной записи сессии."""

    model_config = ConfigDict(extra="forbid")

    purpose: Literal["api", "sse"]


class AccessTokenResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str
    expires_in: int
    token_type: Literal["Bearer"] = "Bearer"


class CurrentUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    is_admin: bool
    created_at: datetime


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=512)]
    new_password: Annotated[str, StringConstraints(strict=True, min_length=12, max_length=512)]


class DeleteAccountRequest(BaseModel):
    """Повторное подтверждение личности для чувствительной операции."""

    model_config = ConfigDict(extra="forbid")

    password: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=512)]


# --- Проекты -------------------------------------------------------------


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[
        str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=255)
    ]
    description: Annotated[str, StringConstraints(strict=True, max_length=10_000)] | None = None


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str | None
    created_at: datetime


# --- Задачи --------------------------------------------------------------


def _attributes_byte_limit(value: dict) -> dict:
    limit = get_settings().max_attributes_bytes
    if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > limit:
        raise ValueError(f"Атрибуты превышают {limit // 1024} KiB")
    return value


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: PositiveId
    attributes: dict[AttributeCode, StrictStr | StrictBool | None] = Field(max_length=64)

    @field_validator("attributes")
    @classmethod
    def check_size(cls, value: dict) -> dict:
        return _attributes_byte_limit(value)


class TaskUpdate(BaseModel):
    """Полная замена набора атрибутов: JSONB присваивается новым словарём."""

    model_config = ConfigDict(extra="forbid")

    attributes: dict[AttributeCode, StrictStr | StrictBool | None] = Field(max_length=64)

    @field_validator("attributes")
    @classmethod
    def check_size(cls, value: dict) -> dict:
        return _attributes_byte_limit(value)


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    attributes: dict[str, str | bool]
    created_at: datetime
    updated_at: datetime


# --- Метаданные атрибутов ------------------------------------------------


class MetaField(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: AttributeCode
    type: Literal["string", "boolean", "date"]
    is_required: StrictBool


class MetaFieldResponse(MetaField):
    title: str


class MetaFieldCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: AttributeCode
    title: Annotated[
        str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=100)
    ]
    type: Literal["string", "boolean", "date"]
    is_required: StrictBool = False

    @field_validator("code")
    @classmethod
    def not_reserved(cls, value: str) -> str:
        if value in RESERVED_ATTRIBUTE_CODES:
            raise ValueError("Код конфликтует с API BaseModel")
        return value


# --- Динамическая схема атрибутов ---------------------------------------


@lru_cache(maxsize=256)
def attributes_model(signature: tuple[tuple[str, str, bool], ...]) -> type[BaseModel]:
    """Кэш ограничен и зависит от содержимого метаданных, а не от их количества."""
    settings = get_settings()
    definitions: dict[str, Any] = {}
    for code, field_type, required in signature:
        annotation: Any
        if field_type == "string":
            annotation = Annotated[
                str,
                StringConstraints(
                    strict=True,
                    strip_whitespace=True,
                    min_length=1 if required else 0,
                    max_length=settings.max_attribute_string_length,
                ),
            ]
        elif field_type == "boolean":
            annotation = StrictBool
        elif field_type == "date":
            annotation = ISODate
        else:
            raise ValueError("Неизвестный тип поля в серверных метаданных")

        definitions[code] = (annotation, ...) if required else (annotation | None, None)

    return create_model("TaskAttributes", __config__=ConfigDict(extra="forbid"), **definitions)


def validate_attributes(raw: dict, metadata: list[MetaField]) -> dict:
    """Возвращает нормализованный словарь для JSONB.

    `null` необязательного поля нормализуется в отсутствие ключа; обязательный
    boolean со значением `false` сохраняется.
    """
    signature = tuple(sorted((m.code, m.type, m.is_required) for m in metadata))
    validated = attributes_model(signature).model_validate(raw)
    return validated.model_dump(mode="json", exclude_none=True)
