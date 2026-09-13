"""Cookie сессии и короткоживущая cookie перехода OAuth.

Сессионная cookie покрывает весь `/api/v1`, а не только маршруты входа: ею
авторизуются все запросы, включая SSE и выдачу картинок. Прежняя refresh-cookie
с `Path=/api/v1/auth/` для этого не годилась — до остальных маршрутов она просто
не доходила, и клиенту приходилось обменивать её на токен перед каждым запросом.

Значение недоступно JavaScript (`HttpOnly`), не уходит по HTTP (`Secure`) и не
отправляется в кросс-сайтовых запросах (`SameSite=Strict`).
"""

from fastapi import Response

from .config import Settings

SESSION_COOKIE = "__Secure-todo_session"
# Имя с префиксом __Secure- требует Secure и работает только по HTTPS.
INSECURE_SESSION_COOKIE = "todo_session"
SESSION_PATH = "/api/v1"

# Префикс __Host- дополнительно запрещает установку cookie с соседнего
# поддомена, но требует Path=/ — поэтому имя отличается от сессионной cookie.
OAUTH_STATE_COOKIE = "__Host-oauth_state"
INSECURE_OAUTH_STATE_COOKIE = "oauth_state"
OAUTH_STATE_PATH = "/"


def session_cookie_name(settings: Settings) -> str:
    return SESSION_COOKIE if settings.cookie_secure else INSECURE_SESSION_COOKIE


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


def set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    """Срок хранения в браузере — абсолютный срок сессии.

    Предел простоя короче и проверяется на сервере: срок cookie не может быть
    авторитетом, его выставляет и меняет клиент.
    """
    response.set_cookie(
        key=session_cookie_name(settings),
        value=token,
        max_age=settings.session_absolute_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path=SESSION_PATH,
    )
    response.headers["Cache-Control"] = "no-store"


def clear_session_cookie(response: Response, settings: Settings) -> None:
    """Удаление выполняется с теми же именем и Path, что и установка."""
    response.delete_cookie(
        session_cookie_name(settings),
        path=SESSION_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
    )
    response.headers["Cache-Control"] = "no-store"
