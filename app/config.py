"""Конфигурация приложения. Значения читаются из окружения / .env."""

from functools import lru_cache

from pydantic import Field, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Хранилища -------------------------------------------------------
    database_url: PostgresDsn = Field(default="postgresql+psycopg://todo:todo@127.0.0.1:55433/todo")
    redis_url: RedisDsn = Field(default="redis://127.0.0.1:56379/0")

    # Префикс всех ключей и Pub/Sub каналов. Номер Redis DB не изолирует
    # Pub/Sub, поэтому среды разделяются префиксом (или отдельным сервером).
    redis_namespace: str = Field(default="todo:dev", min_length=1, max_length=64)

    db_pool_size: int = 10
    db_max_overflow: int = 10
    redis_max_connections: int = 50
    # Отдельный пул для SSE: каждая подписка занимает соединение целиком.
    redis_pubsub_max_connections: int = 200

    # --- Origin / CORS ---------------------------------------------------
    # SPA и API обслуживаются с одного HTTPS origin; точное совпадение.
    allowed_origin: str = Field(default="http://localhost:5173")

    # --- Токены и сессии -------------------------------------------------
    token_ttl_seconds: int = 300
    session_absolute_ttl_seconds: int = 12 * 3600
    sse_stream_seconds: int = 240
    sse_revocation_check_seconds: int = 5

    # --- Пароли ----------------------------------------------------------
    password_min_length: int = 12
    argon2_time_cost: int = 3
    argon2_memory_cost: int = 64 * 1024
    argon2_parallelism: int = 4

    # --- Rate limit (фиксированное окно) ---------------------------------
    login_rate_limit: int = 10
    login_rate_window_seconds: int = 300
    refresh_rate_limit: int = 240
    refresh_rate_window_seconds: int = 60

    # --- Лимиты данных ---------------------------------------------------
    max_attributes: int = 64
    max_attribute_string_length: int = 2_000
    max_attributes_bytes: int = 65_536
    max_request_body_bytes: int = 128 * 1024
    export_max_tasks: int = 5_000
    idempotency_ttl_seconds: int = 24 * 3600

    # secure=True для cookie; отключается только в локальной HTTP-разработке.
    cookie_secure: bool = True

    @property
    def key_prefix(self) -> str:
        return f"{self.redis_namespace}:"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
