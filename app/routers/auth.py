"""Регистрация, вход, подтверждение кодом и выход.

Пароль сам по себе сессию не создаёт: и вход, и регистрация завершаются только
после подтверждения одноразовым кодом из письма. Успешное подтверждение
устанавливает сессионную cookie; никакого токена клиент не получает.

Обмена refresh на access больше нет: он выполнялся перед каждым защищённым
запросом, выстраивал параллельные запросы в очередь и создавал строку в БД на
каждый из них.

Все небезопасные методы `/api/v1` защищены проверкой точного Origin и
обязательного заголовка `X-CSRF-Guard: 1` (см. main.py). Маршруты OAuth вынесены
в отдельный роутер: они открываются переходом по ссылке, где этих заголовков не
бывает, и защищены параметром `state` вместе с cookie, связывающей его с
начавшим вход браузером.
"""

import logging
import secrets

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, oauth, otp
from ..config import Settings
from ..cookies import (
    clear_oauth_state_cookie,
    clear_session_cookie,
    oauth_state_cookie_name,
    session_cookie_name,
    set_oauth_state_cookie,
    set_session_cookie,
)
from ..dependencies import Db, MailerDep, RedisDep, SettingsDep
from ..models import OAuthAccount, User
from ..schemas import (
    CurrentUserResponse,
    LoginRequest,
    OAuthProvidersResponse,
    OtpChallengeResponse,
    OtpVerifyRequest,
    RegisterRequest,
)
from ..security import client_ip, enforce_rate_limit, hash_password, verify_password
from ..sessions import create_session, revoke_by_token

router = APIRouter(prefix="/auth", tags=["auth"])
oauth_router = APIRouter(prefix="/auth/oauth", tags=["auth"])

logger = logging.getLogger(__name__)
NO_STORE = {"Cache-Control": "no-store"}
PROVIDERS = ("google", "github")
# Свой state — 32 символа; ограничение отсекает мусор до сравнения.
MAX_STATE_LENGTH = 128


async def _sign_in(response: Response, db: AsyncSession, settings: Settings, user: User) -> User:
    """Завершает вход: новая сессия и cookie. Тело ответа — сам пользователь.

    Никакого токена клиенту не выдаётся: значение сессии живёт только в
    HttpOnly cookie, поэтому XSS не может его прочитать, а JavaScript —
    отправить куда-либо, кроме своего origin.
    """
    raw, _ = await create_session(db, settings, user.id)
    await db.commit()
    set_session_cookie(response, settings, raw)
    response.headers["Cache-Control"] = "no-store"
    return user


@router.post("/register", status_code=status.HTTP_202_ACCEPTED, response_model=OtpChallengeResponse)
async def register(
    payload: RegisterRequest,
    request: Request,
    db: Db,
    redis: RedisDep,
    settings: SettingsDep,
    mailer: MailerDep,
):
    """Пароль принят и отложен: аккаунт создаётся только после ввода кода."""
    email = payload.email.lower()
    await enforce_rate_limit(
        redis,
        settings,
        f"otp:ip:{client_ip(request)}",
        settings.otp_request_limit,
        settings.otp_request_window_seconds,
    )
    await enforce_rate_limit(
        redis,
        settings,
        f"otp:email:{email}",
        settings.otp_request_limit,
        settings.otp_request_window_seconds,
    )
    if len(payload.password) < settings.password_min_length:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Пароль короче {settings.password_min_length} символов",
        )
    if await db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email уже зарегистрирован")

    await otp.send_code(
        db,
        settings,
        mailer,
        redis,
        email,
        "register",
        password_hash=await hash_password(payload.password),
    )
    return {"otp_required": True, "purpose": "register", "expires_in": settings.otp_ttl_seconds}


@router.post("/login", status_code=status.HTTP_202_ACCEPTED, response_model=OtpChallengeResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    db: Db,
    redis: RedisDep,
    settings: SettingsDep,
    mailer: MailerDep,
):
    """Проверяет пароль и отправляет код: сессия появляется только после кода."""
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
    # verify_password сравнивается с фиктивным хешем, если пользователя нет или
    # у него только OAuth: время ответа не выдаёт существование аккаунта.
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

    await otp.send_code(db, settings, mailer, redis, email, "login")
    return {"otp_required": True, "purpose": "login", "expires_in": settings.otp_ttl_seconds}


@router.post("/otp/verify", response_model=CurrentUserResponse)
async def verify_otp(
    payload: OtpVerifyRequest,
    request: Request,
    response: Response,
    db: Db,
    redis: RedisDep,
    settings: SettingsDep,
):
    """Проверяет код и завершает вход или регистрацию."""
    email = payload.email.lower()
    await enforce_rate_limit(
        redis,
        settings,
        f"otp:verify:{client_ip(request)}",
        settings.login_rate_limit,
        settings.login_rate_window_seconds,
    )

    try:
        record = await otp.consume_code(db, settings, email, payload.code, payload.purpose)
    except otp.OtpError as error:
        # Счётчик попыток и гашение фиксируются до отказа, иначе перебор
        # не оставлял бы следов.
        await db.commit()
        await audit.write_audit(audit.LOGIN_FAILED, detail=f"otp:{error}")
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Неверный или истёкший код", headers=NO_STORE
        ) from error

    if payload.purpose == "register":
        user = User(email=email, hashed_password=record.pending_password_hash)
        db.add(user)
        try:
            await db.flush()
        except IntegrityError as exc:
            await db.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, "Email уже зарегистрирован") from exc
    else:
        user = await db.scalar(select(User).where(User.email == email))
        if user is None or not user.is_active:
            await db.commit()
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Сессия недействительна")

    return await _sign_in(response, db, settings, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, db: Db, settings: SettingsDep):
    """Отзывает сессию и удаляет cookie. Ответ одинаков независимо от результата.

    Одинаковый ответ на известное и неизвестное значение обязателен: иначе
    выходом можно было бы проверять чужие значения на существование.
    """
    presented = request.cookies.get(session_cookie_name(settings))
    await revoke_by_token(db, presented, "logout")
    clear_session_cookie(response, settings)


# --- OAuth ---------------------------------------------------------------


@oauth_router.get("/providers", response_model=OAuthProvidersResponse)
async def list_providers(settings: SettingsDep):
    """Какие кнопки показывать: провайдер без client_id считается выключенным."""
    return {"providers": settings.enabled_oauth_providers}


@oauth_router.get("/{provider}/start")
async def oauth_start(provider: str, redis: RedisDep, settings: SettingsDep):
    if provider not in PROVIDERS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Неизвестный провайдер")
    try:
        url, state = await oauth.start(redis, settings, provider)
    except oauth.OAuthError as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error
    redirect = RedirectResponse(url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    # Одна cookie — один незавершённый вход на браузер: начатый заново переход
    # вытесняет предыдущий, и его ссылка-возврат перестаёт работать.
    set_oauth_state_cookie(redirect, settings, state)
    return redirect


@oauth_router.get("/{provider}/callback")
async def oauth_callback(
    request: Request,
    provider: str,
    db: Db,
    redis: RedisDep,
    settings: SettingsDep,
    code: str = "",
    state: str = "",
    error: str = "",
):
    """Возврат от провайдера: обмен кода, поиск или создание пользователя, сессия."""
    spa = settings.oauth_redirect_base

    def failure(reason: str) -> RedirectResponse:
        # Причина остаётся в логе: в URL она подсказывала бы атакующему,
        # какой именно шаг не прошёл.
        logger.info("OAuth %s не удался: %s", provider, reason)
        redirect = RedirectResponse(f"{spa}/login?oauth_error=1", status_code=303)
        clear_oauth_state_cookie(redirect, settings)
        return redirect

    if provider not in PROVIDERS or error or not code:
        return failure(error or "нет кода")

    # Случайность и одноразовость state закрывают подбор и повтор, но не
    # перенос ещё не использованной ссылки-возврата в чужой браузер: там она
    # молча завершила бы вход в аккаунт атакующего. Поэтому переход
    # засчитывается только тому браузеру, который его начал.
    bound_state = request.cookies.get(oauth_state_cookie_name(settings), "")
    if (
        not state
        or len(state) > MAX_STATE_LENGTH
        or not bound_state
        # compare_digest на str падает на не-ASCII, а state приходит из URL.
        or not secrets.compare_digest(bound_state.encode(), state.encode())
    ):
        return failure("state не связан с браузером")

    try:
        verifier = await oauth.consume_state(redis, settings, provider, state)
        identity = await oauth.exchange(settings, provider, code, verifier)
    except oauth.OAuthError as exc:
        return failure(str(exc))

    if not identity.account_id:
        return failure("провайдер не вернул идентификатор аккаунта")

    account = await db.scalar(
        select(OAuthAccount).where(
            OAuthAccount.provider == provider,
            OAuthAccount.provider_account_id == identity.account_id,
        )
    )
    if account is not None:
        user = await db.get(User, account.user_id)
        if user is None or not user.is_active:
            return failure("аккаунт отключён")
    else:
        user = None
        if identity.email and identity.email_verified:
            # Связывание с существующим аккаунтом — только по подтверждённому
            # провайдером адресу.
            user = await db.scalar(select(User).where(User.email == identity.email.lower()))
        if user is None:
            if not identity.email:
                return failure("провайдер не вернул адрес почты")
            taken = await db.scalar(select(User.id).where(User.email == identity.email.lower()))
            if taken is not None:
                # Адрес занят, но провайдер его не подтвердил: связывать нельзя —
                # так чужой аккаунт угонялся бы регистрацией на тот же адрес.
                return failure("адрес занят и не подтверждён провайдером")
            user = User(
                email=identity.email.lower(),
                hashed_password=None,
                display_name=identity.display_name,
                avatar_url=identity.avatar_url,
            )
            db.add(user)
            await db.flush()
        db.add(
            OAuthAccount(
                provider=provider,
                provider_account_id=identity.account_id,
                user_id=user.id,
                email=identity.email,
            )
        )
        await db.flush()

    audit.add_audit(db, audit.LOGIN_OK, user_id=user.id, detail=f"oauth:{provider}")
    raw, _ = await create_session(db, settings, user.id)
    await db.commit()

    redirect = RedirectResponse(f"{spa}/projects", status_code=303)
    set_session_cookie(redirect, settings, raw)
    clear_oauth_state_cookie(redirect, settings)
    return redirect
