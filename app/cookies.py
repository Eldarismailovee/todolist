"""Refresh cookie. Устанавливается auth-сервисом только после успешной ротации."""

from fastapi import Response

from .config import Settings

REFRESH_COOKIE = "__Secure-refresh_token"
# Имя с префиксом __Secure- требует Secure и работает только по HTTPS.
INSECURE_REFRESH_COOKIE = "refresh_token"
REFRESH_PATH = "/api/v1/auth/"


def refresh_cookie_name(settings: Settings) -> str:
    return REFRESH_COOKIE if settings.cookie_secure else INSECURE_REFRESH_COOKIE


def set_refresh_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        key=refresh_cookie_name(settings),
        value=token,
        max_age=settings.token_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path=REFRESH_PATH,
    )
    response.headers["Cache-Control"] = "no-store"


def clear_refresh_cookie(response: Response, settings: Settings) -> None:
    """Удаление выполняется с теми же именем и Path, что и установка."""
    response.delete_cookie(
        refresh_cookie_name(settings),
        path=REFRESH_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
    )
    response.headers["Cache-Control"] = "no-store"
