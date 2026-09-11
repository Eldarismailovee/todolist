"""Refresh cookie. Устанавливается auth-сервисом только после успешной ротации.

Здесь же короткоживущая cookie, связывающая начатый OAuth-переход с браузером.
"""

from fastapi import Response

from .config import Settings

REFRESH_COOKIE = "__Secure-refresh_token"
# Имя с префиксом __Secure- требует Secure и работает только по HTTPS.
INSECURE_REFRESH_COOKIE = "refresh_token"
REFRESH_PATH = "/api/v1/auth/"

# Префикс __Host- дополнительно запрещает установку cookie с соседнего
# поддомена, но требует Path=/ — поэтому имя отличается от refresh cookie.
OAUTH_STATE_COOKIE = "__Host-oauth_state"
INSECURE_OAUTH_STATE_COOKIE = "oauth_state"
OAUTH_STATE_PATH = "/"


def refresh_cookie_name(settings: Settings) -> str:
    return REFRESH_COOKIE if settings.cookie_secure else INSECURE_REFRESH_COOKIE


def oauth_state_cookie_name(settings: Settings) -> str:
    return OAUTH_STATE_COOKIE if settings.cookie_secure else INSECURE_OAUTH_STATE_COOKIE


def set_oauth_state_cookie(response: Response, settings: Settings, state: str) -> None:
    """Связывает state с браузером, начавшим вход.

    SameSite=lax, а не strict: возврат от провайдера — top-level GET с чужого
    сайта, при strict cookie до него не дойдёт. Живёт столько же, сколько
    запись state в Redis.
    """
    response.set_cookie(
        key=oauth_state_cookie_name(settings),
        value=state,
        max_age=settings.oauth_state_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path=OAUTH_STATE_PATH,
    )
    response.headers["Cache-Control"] = "no-store"


def clear_oauth_state_cookie(response: Response, settings: Settings) -> None:
    """Удаление выполняется с теми же именем и Path, что и установка."""
    response.delete_cookie(
        oauth_state_cookie_name(settings),
        path=OAUTH_STATE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"


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
