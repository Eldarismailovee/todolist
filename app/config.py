"""Конфигурация приложения. Значения читаются из окружения / .env."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "production"]

# Значение ключа из репозитория: подписанное им нельзя считать секретом.
DEFAULT_SECRET_KEY = "dev-only-insecure-secret-change-me"
MIN_SECRET_KEY_LENGTH = 32

# Запас общего лимита тела над суммой полевых лимитов JSON: заголовок,
# описание, идентификаторы и экранирование не-ASCII символов.
JSON_BODY_RESERVE_BYTES = 256 * 1024


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Среда -----------------------------------------------------------
    # Удобные для разработки послабления (известный ключ, cookie без Secure,
    # код в логе, служебные маршруты) в production не предупреждение, а отказ
    # запускаться: см. _check_production_safety.
    environment: Environment = "development"

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
    # Сроки разделены по назначению: одно значение на все credentials создавало
    # видимость правила «всё живёт пять минут», которого на деле не было —
    # OTP, state и ссылки на файлы всегда жили дольше. Границы заданы явно,
    # чтобы окружение не могло тихо превратить короткий токен в долгий.
    access_token_ttl_seconds: int = Field(default=300, ge=30, le=900)
    refresh_token_ttl_seconds: int = Field(default=300, ge=60, le=24 * 3600)
    session_absolute_ttl_seconds: int = Field(default=12 * 3600, ge=300, le=30 * 24 * 3600)
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

    # Ключ для HMAC коротких значений (OTP). Шестизначный код слишком мал для
    # обычного хеша: без секрета его подобрали бы по утёкшей базе за секунды.
    # SecretStr прячет значение в repr настроек и трассировках; это не фильтр
    # сторонних логов, поэтому сам ключ берётся только в месте вычисления HMAC.
    secret_key: SecretStr = SecretStr(DEFAULT_SECRET_KEY)

    # --- Одноразовые коды (OTP) ------------------------------------------
    otp_length: int = 6
    # Код вводит человек из письма: минуты, а не секунды, но и не часы.
    otp_ttl_seconds: int = Field(default=600, ge=60, le=1800)
    otp_max_attempts: int = 5
    # Запросов кода на один адрес за окно: иначе почтой можно завалить чужой ящик.
    otp_request_limit: int = 5
    otp_request_window_seconds: int = 900
    # Вывести код в лог удобно локально и недопустимо в production.
    otp_log_codes: bool = False
    # Подключает служебные маршруты для e2e (чтение последнего кода).
    # По умолчанию выключено: в production этих путей просто нет.
    enable_testing_endpoints: bool = False

    # --- OAuth -----------------------------------------------------------
    # URL провайдеров вынесены в настройки, чтобы тесты могли подставить
    # локальный фейковый провайдер, не подменяя код.
    oauth_redirect_base: str = Field(default="http://localhost:5173")
    google_client_id: str = ""
    google_client_secret: str = ""
    google_authorize_url: str = "https://accounts.google.com/o/oauth2/v2/auth"
    google_token_url: str = "https://oauth2.googleapis.com/token"
    google_userinfo_url: str = "https://openidconnect.googleapis.com/v1/userinfo"
    github_client_id: str = ""
    github_client_secret: str = ""
    github_authorize_url: str = "https://github.com/login/oauth/authorize"
    github_token_url: str = "https://github.com/login/oauth/access_token"
    github_userinfo_url: str = "https://api.github.com/user"
    github_emails_url: str = "https://api.github.com/user/emails"
    # Время на один переход к провайдеру и обратно. Тот же срок живёт cookie,
    # связывающая state с браузером, поэтому запас держится небольшим.
    oauth_state_ttl_seconds: int = Field(default=300, ge=60, le=900)

    # --- Почта -----------------------------------------------------------
    # Без smtp_host письма пишутся в лог: локальная разработка не требует
    # настоящего почтового сервера.
    smtp_host: str = ""
    smtp_port: int = 1026
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = False
    smtp_start_tls: bool = False
    mail_from: str = "Todo App <no-reply@todo.local>"
    mail_timeout_seconds: float = 10.0

    # --- Telegram --------------------------------------------------------
    telegram_bot_token: str = ""
    telegram_api_base: str = "https://api.telegram.org"

    # --- AI-ассистент ----------------------------------------------------
    anthropic_api_key: str = ""
    ai_model: str = "claude-opus-5"
    # Занижать max_tokens нельзя: ответ обрежется на середине мысли.
    ai_max_tokens: int = 4_000
    # Задачи ассистента простые (переписать, сжать, разбить на шаги), поэтому
    # низкое усилие; мышление остаётся адаптивным.
    ai_effort: str = "low"
    ai_input_limit: int = 20_000
    ai_rate_limit: int = 30
    ai_rate_window_seconds: int = 3600

    # --- Загрузка файлов -------------------------------------------------
    upload_dir: str = "var/uploads"
    # Ссылка на картинку живёт дольше access-токена намеренно: её открывает
    # тег <img> при каждом показе задачи. Значение задано здесь, а не константой
    # в коде подписи, чтобы срок был виден вместе с остальными.
    attachment_url_ttl_seconds: int = Field(default=3600, ge=60, le=24 * 3600)
    max_upload_bytes: int = 5 * 1024 * 1024
    # Границы, заголовки частей и имя файла идут в теле поверх самого файла.
    # Общий лимит тела считает их вместе с содержимым, поэтому запас нужен
    # явный: без него файл ровно на max_upload_bytes не пролезал бы.
    multipart_overhead_bytes: int = 64 * 1024
    allowed_upload_types: tuple[str, ...] = (
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "image/svg+xml",
    )

    # --- Уведомления о дедлайнах -----------------------------------------
    notification_poll_seconds: int = 60
    notification_batch_size: int = 200

    # --- Лимиты данных ---------------------------------------------------
    max_attributes: int = 64
    max_attribute_string_length: int = 2_000
    max_attributes_bytes: int = 65_536
    # Грубый потолок против чтения бесконечного потока — не подмена точных
    # проверок полей. Он обязан быть больше суммы полевых лимитов, иначе
    # заявленный размер содержимого недостижим: запрос отвергается на
    # middleware ещё до валидации и пользователь получает 413 вместо 422.
    # Запас в JSON_BODY_RESERVE_BYTES покрывает остальные поля и \uXXXX-
    # экранирование, которым клиент вправе передать кириллицу.
    max_request_body_bytes: int = 2 * 1024 * 1024
    max_content_bytes: int = 512 * 1024
    export_max_tasks: int = 5_000
    idempotency_ttl_seconds: int = 24 * 3600

    # secure=True для cookie; отключается только в локальной HTTP-разработке.
    cookie_secure: bool = True

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def uses_default_secret_key(self) -> bool:
        return self.secret_key.get_secret_value() == DEFAULT_SECRET_KEY

    @property
    def max_upload_body_bytes(self) -> int:
        """Лимит тела для загрузки файла: сам файл плюс обвязка multipart."""
        return self.max_upload_bytes + self.multipart_overhead_bytes

    @model_validator(mode="after")
    def _check_body_limits(self) -> "Settings":
        """Обещанные лимиты полей должны быть достижимы через общий лимит тела.

        Несогласованность здесь не видна в коде: маршрут объявляет 5 МиБ,
        middleware молча отвергает запрос раньше. Поэтому конфигурация, в
        которой полевой лимит недостижим, не должна подниматься.
        """
        required = self.max_content_bytes + self.max_attributes_bytes + JSON_BODY_RESERVE_BYTES
        if self.max_request_body_bytes < required:
            raise ValueError(
                f"MAX_REQUEST_BODY_BYTES={self.max_request_body_bytes} меньше {required}: "
                f"содержимое ({self.max_content_bytes}) и атрибуты "
                f"({self.max_attributes_bytes}) не пройдут общий лимит тела"
            )
        return self

    @model_validator(mode="after")
    def _check_production_safety(self) -> "Settings":
        """В production опасная конфигурация — отказ старта, а не предупреждение.

        Предупреждение в логе не мешает выкатить сборку с ключом по умолчанию
        или включёнными служебными маршрутами: его никто не читает до инцидента.
        Процесс не должен подниматься вовсе.
        """
        if not self.is_production:
            return self

        problems: list[str] = []
        secret = self.secret_key.get_secret_value()
        if self.uses_default_secret_key:
            problems.append("SECRET_KEY остался значением по умолчанию из репозитория")
        elif len(secret) < MIN_SECRET_KEY_LENGTH:
            problems.append(f"SECRET_KEY короче {MIN_SECRET_KEY_LENGTH} символов")
        if not self.cookie_secure:
            problems.append("COOKIE_SECURE=false: refresh cookie уйдёт по открытому HTTP")
        if self.otp_log_codes:
            problems.append("OTP_LOG_CODES=true: коды подтверждения попадут в журнал")
        if self.enable_testing_endpoints:
            problems.append("ENABLE_TESTING_ENDPOINTS=true: код подтверждения читается по HTTP")
        if not self.smtp_host:
            # Вход всегда завершается кодом из письма: без SMTP код уходит в
            # лог-заглушку, то есть подтверждение перестаёт быть подтверждением.
            problems.append("SMTP_HOST не задан: письма с кодами пишутся в лог вместо отправки")

        if problems:
            raise ValueError(
                "Конфигурация непригодна для ENVIRONMENT=production:\n"
                + "\n".join(f"  - {problem}" for problem in problems)
            )
        return self

    @property
    def key_prefix(self) -> str:
        return f"{self.redis_namespace}:"

    def oauth_provider(self, provider: str) -> dict[str, str]:
        """Настройки провайдера одним словарём; пустой client_id = выключен."""
        if provider == "google":
            return {
                "client_id": self.google_client_id,
                "client_secret": self.google_client_secret,
                "authorize_url": self.google_authorize_url,
                "token_url": self.google_token_url,
                "userinfo_url": self.google_userinfo_url,
                "scope": "openid email profile",
            }
        if provider == "github":
            return {
                "client_id": self.github_client_id,
                "client_secret": self.github_client_secret,
                "authorize_url": self.github_authorize_url,
                "token_url": self.github_token_url,
                "userinfo_url": self.github_userinfo_url,
                "scope": "read:user user:email",
            }
        raise ValueError(f"Неизвестный провайдер: {provider}")

    @property
    def enabled_oauth_providers(self) -> list[str]:
        return [name for name in ("google", "github") if self.oauth_provider(name)["client_id"]]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
