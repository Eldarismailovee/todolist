"""Регистрация, вход, ротация refresh и выход.

Login, refresh и logout защищены проверкой точного Origin и обязательного
заголовка `X-CSRF-Guard: 1`.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .. import audit, auth_service
from ..config import Settings
from ..cookies import clear_refresh_cookie, refresh_cookie_name, set_refresh_cookie
from ..dependencies import Db, RedisDep, SettingsDep
from ..models import User
from ..schemas import (
    AccessTokenResponse,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
)
from ..security import (
    client_ip,
    enforce_rate_limit,
    hash_password,
    require_csrf_guard,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[Depends(require_csrf_guard)])

NO_STORE = {"Cache-Control": "no-store"}


def _token_response(
    response: Response, tokens: auth_service.IssuedTokens, settings: Settings
) -> dict:
    set_refresh_cookie(response, settings, tokens.refresh_token)
    response.headers["Cache-Control"] = "no-store"
    # Refresh token в теле ответа не передаётся.
    return {
        "access_token": tokens.access_token,
        "expires_in": tokens.expires_in,
        "token_type": "Bearer",
    }


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=AccessTokenResponse)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: Db,
    redis: RedisDep,
    settings: SettingsDep,
):
    await enforce_rate_limit(
        redis,
        settings,
        f"register:{client_ip(request)}",
        settings.login_rate_limit,
        settings.login_rate_window_seconds,
    )
    if len(payload.password) < settings.password_min_length:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Пароль короче {settings.password_min_length} символов",
        )
    email = payload.email.lower()
    user = User(email=email, hashed_password=await hash_password(payload.password))
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Email уже зарегистрирован") from exc

    tokens = await auth_service.start_session(db, redis, settings, user)
    return _token_response(response, tokens, settings)


@router.post("/login", response_model=AccessTokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Db,
    redis: RedisDep,
    settings: SettingsDep,
):
    email = payload.email.lower()
    await enforce_rate_limit(
        redis,
        settings,
        f"login:ip:{client_ip(request)}",
        settings.login_rate_limit,
        settings.login_rate_window_seconds,
    )
    await enforce_rate_limit(
        redis,
        settings,
        f"login:email:{email}",
        settings.login_rate_limit,
        settings.login_rate_window_seconds,
    )

    user = await db.scalar(select(User).where(User.email == email))
    # verify_password сравнивается с фиктивным хешем, если пользователя нет.
    if not await verify_password(user.hashed_password if user else None, payload.password):
        await audit.write_audit(
            audit.LOGIN_FAILED, user_id=user.id if user else None, detail="bad_credentials"
        )
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Неверный email или пароль", headers=NO_STORE
        )
    if not user.is_active:
        await audit.write_audit(audit.LOGIN_FAILED, user_id=user.id, detail="inactive")
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Неверный email или пароль", headers=NO_STORE
        )

    tokens = await auth_service.start_session(db, redis, settings, user)
    return _token_response(response, tokens, settings)


@router.post("/refresh", response_model=AccessTokenResponse)
async def refresh(
    payload: RefreshRequest,
    request: Request,
    response: Response,
    db: Db,
    redis: RedisDep,
    settings: SettingsDep,
):
    """Один обмен refresh на новый access указанного назначения и новый refresh."""
    await enforce_rate_limit(
        redis,
        settings,
        f"refresh:ip:{client_ip(request)}",
        settings.refresh_rate_limit,
        settings.refresh_rate_window_seconds,
    )
    presented = request.cookies.get(refresh_cookie_name(settings))
    if not presented:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Нет refresh cookie", headers=NO_STORE)
    tokens = await auth_service.rotate_refresh(db, redis, settings, presented, payload.purpose)
    return _token_response(response, tokens, settings)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    db: Db,
    settings: SettingsDep,
):
    """Отзывает сессию и удаляет cookie. Ответ одинаков независимо от результата."""
    presented = request.cookies.get(refresh_cookie_name(settings))
    await auth_service.logout_by_refresh(db, presented)
    clear_refresh_cookie(response, settings)
