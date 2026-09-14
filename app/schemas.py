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
    AwareDatetime,
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
    model_validator,
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


class OtpChallengeResponse(BaseModel):
    """Ответ на шаг «пароль принят»: сессии ещё нет, нужен код из письма."""

    model_config = ConfigDict(extra="forbid")

    otp_required: Literal[True] = True
    purpose: Literal["login", "register"]
    expires_in: int


class DeleteCodeChallengeResponse(BaseModel):
    """Код подтверждения удаления аккаунта отправлен.

    Отдельный тип, а не расширение OtpChallengeResponse: цель этого кода не
    входит в допустимые значения /auth/otp/verify, и контракт входа не должен
    объявлять её как возможный ответ.
    """

    model_config = ConfigDict(extra="forbid")

    otp_required: Literal[True] = True
    purpose: Literal["delete_account"] = "delete_account"
    expires_in: int


class OtpVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    code: Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9]{4,10}$")]
    purpose: Literal["login", "register"]


class OAuthProvidersResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: list[Literal["google", "github"]]


class CurrentUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    is_admin: bool
    display_name: str | None = None
    avatar_url: str | None = None
    # Значения по умолчанию у этого поля быть не должно: оно вычисляется из
    # модели, а умолчание скрывало отсутствие свойства.
    has_password: bool
    created_at: datetime


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=512)]
    new_password: Annotated[str, StringConstraints(strict=True, min_length=12, max_length=512)]


class DeleteAccountRequest(BaseModel):
    """Повторное подтверждение личности для чувствительной операции.

    Пароль есть не у всех: аккаунт, созданный через OAuth, иначе невозможно
    удалить вовсе. Для него подтверждением служит одноразовый код на
    подтверждённый адрес, запрошенный отдельно и привязанный к этому действию.
    Пропускать проверку для OAuth-аккаунтов нельзя: удаление данных должно
    требовать свежего подтверждения личности.
    """

    model_config = ConfigDict(extra="forbid")

    password: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=512)] = Field(
        default=None
    )
    code: Annotated[str, StringConstraints(strict=True, min_length=4, max_length=12)] = Field(
        default=None
    )

    @model_validator(mode="after")
    def exactly_one_proof(self) -> "DeleteAccountRequest":
        if (self.password is None) == (self.code is None):
            raise ValueError("Нужен ровно один способ подтверждения: password или code")
        return self


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


def _content_byte_limit(value: dict | None) -> dict | None:
    if value is None:
        return None
    limit = get_settings().max_content_bytes
    if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > limit:
        raise ValueError(f"Содержимое превышает {limit // 1024} KiB")
    return value


def require_datetime_string(value: Any) -> Any:
    """Момент времени передаётся строкой ISO-8601, а не числом.

    Число Pydantic принял бы как Unix timestamp и подставил бы UTC, то есть
    отсутствие зоны у клиента превратилось бы в молчаливое допущение сервера.
    """
    if not isinstance(value, str):
        raise ValueError("Ожидается ISO-строка даты и времени с часовым поясом")
    return value


# Срок задачи всегда с зоной: naive-значение в БД истолковывается по её
# настройкам, и «18:00» пользователя из другого часового пояса уезжает.
DueAt = Annotated[AwareDatetime, BeforeValidator(require_datetime_string)]

TaskTitle = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=255)
]
Attributes = Annotated[dict[AttributeCode, StrictStr | StrictBool | None], Field(max_length=64)]


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: PositiveId
    title: TaskTitle
    description: Annotated[str, StringConstraints(strict=True, max_length=10_000)] | None = None
    # Документ Tiptap как есть; текст для поиска извлекает сервер.
    content: dict[str, Any] | None = None
    due_at: DueAt | None = None
    column_id: PositiveId | None = None
    category_id: PositiveId | None = None
    tag_ids: Annotated[list[PositiveId], Field(max_length=32)] = Field(default_factory=list)
    attributes: Attributes = Field(default_factory=dict)

    @field_validator("attributes")
    @classmethod
    def check_attributes_size(cls, value: dict) -> dict:
        return _attributes_byte_limit(value)

    @field_validator("content")
    @classmethod
    def check_content_size(cls, value: dict | None) -> dict | None:
        return _content_byte_limit(value)


class TaskUpdate(BaseModel):
    """Частичное обновление: поле меняется, только если явно передано.

    `attributes` заменяются целиком — JSONB присваивается новым словарём.

    Null принимается только там, где очистка значения осмысленна: описание,
    содержимое, срок, колонка и категория. Для заголовка, тегов, атрибутов и
    признака выполнения null операцией не является — раньше он молча
    игнорировался, и клиент не мог отличить его от применённого изменения.
    """

    model_config = ConfigDict(extra="forbid")

    title: TaskTitle = Field(default=None)
    description: Annotated[str, StringConstraints(strict=True, max_length=10_000)] | None = None
    content: dict[str, Any] | None = None
    due_at: DueAt | None = None
    column_id: PositiveId | None = None
    category_id: PositiveId | None = None
    tag_ids: Annotated[list[PositiveId], Field(max_length=32)] = Field(default=None)
    attributes: Attributes = Field(default=None)
    completed: StrictBool = Field(default=None)

    @field_validator("attributes")
    @classmethod
    def check_attributes_size(cls, value: dict) -> dict:
        return _attributes_byte_limit(value)

    @field_validator("content")
    @classmethod
    def check_content_size(cls, value: dict | None) -> dict | None:
        return _content_byte_limit(value)


class TaskMove(BaseModel):
    """Перетаскивание: целевая колонка и соседи в ней."""

    model_config = ConfigDict(extra="forbid")

    column_id: PositiveId | None = None
    before_id: PositiveId | None = None
    after_id: PositiveId | None = None


class TagResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    color: str


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    column_id: int | None
    category_id: int | None
    title: str
    description: str | None
    content: dict[str, Any] | None
    position: float
    due_at: datetime | None
    completed_at: datetime | None
    attributes: dict[str, str | bool]
    tags: list[TagResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    # Номер прочитанного состояния: клиент возвращает его в If-Match, чтобы
    # его правка не затёрла более новое изменение.
    version: int


class BoardColumnCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: PositiveId
    title: Annotated[
        str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=120)
    ]
    is_done_column: StrictBool = False


class BoardColumnUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: (
        Annotated[
            str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=120)
        ]
        | None
    ) = None
    is_done_column: StrictBool | None = None
    before_id: PositiveId | None = None
    after_id: PositiveId | None = None


class BoardColumnResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    title: str
    position: float
    is_done_column: bool


class TagCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[
        str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=40)
    ]
    color: Annotated[str, StringConstraints(strict=True, pattern=r"^#[0-9a-fA-F]{6}$")] = "#64748b"


class CategoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[
        str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=60)
    ]
    color: Annotated[str, StringConstraints(strict=True, pattern=r"^#[0-9a-fA-F]{6}$")] = "#6366f1"


class CategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    color: str


class AttachmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    url: str
    filename: str
    content_type: str
    size_bytes: int


TelegramChatId = Annotated[str, StringConstraints(strict=True, pattern=r"^-?[0-9]{1,32}$")]
LeadTimeMinutes = Annotated[int, Field(strict=True, ge=5, le=10_080)]


class NotificationPrefsSchema(BaseModel):
    """Ответ с настройками. `telegram_chat_id` задаёт сервер после подтверждения."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    email_enabled: StrictBool = True
    telegram_enabled: StrictBool = False
    telegram_chat_id: TelegramChatId | None = None
    lead_time_minutes: LeadTimeMinutes = 60


class NotificationPrefsUpdate(BaseModel):
    """Что клиент вправе менять сам.

    `telegram_chat_id` в тело не входит: чат подключается только через
    подтверждение кодом, иначе в настройки можно было бы записать чужой чат.
    """

    model_config = ConfigDict(extra="forbid")

    email_enabled: StrictBool = True
    telegram_enabled: StrictBool = False
    lead_time_minutes: LeadTimeMinutes = 60


class TelegramLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chat_id: TelegramChatId


class TelegramLinkChallenge(BaseModel):
    """Код отправлен в указанный чат."""

    model_config = ConfigDict(extra="forbid")

    code_sent: Literal[True] = True
    expires_in: int


class TelegramConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Annotated[str, StringConstraints(strict=True, min_length=4, max_length=12)]


class AssistRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["ideas", "summarize", "decompose", "rewrite"]
    text: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=20_000)]


class AssistResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    items: list[str]
    # "stub" означает, что ключ не настроен и ответ сгенерирован без модели.
    provider: Literal["anthropic", "stub"]
    model: str | None = None


class AnalyticsPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str
    created: int
    completed: int


class AnalyticsCategorySlice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    color: str
    total: int
    completed: int


class AnalyticsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    range_days: int
    total: int
    completed: int
    overdue: int
    due_soon: int
    completion_rate: float
    daily: list[AnalyticsPoint]
    by_category: list[AnalyticsCategorySlice]


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
