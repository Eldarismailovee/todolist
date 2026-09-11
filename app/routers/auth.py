"""Регистрация, вход, подтверждение кодом, ротация refresh и выход.

Пароль сам по себе сессию не создаёт: и вход, и регистрация завершаются только
после подтверждения одноразовым кодом из письма.

Login, refresh и logout защищены проверкой точного Origin и обязательного
заголовка `X-CSRF-Guard: 1`. Маршруты OAuth вынесены в отдельный роутер: они
открываются переходом по ссылке, где этих заголовков не бывает, и защищены
параметром `state` вместе с cookie, связывающей его с начавшим вход браузером.
"""

import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, auth_service, oauth, otp
from ..config import Settings
from ..cookies import (
    clear_oauth_state_cookie,
    clear_refresh_cookie,
    oauth_state_cookie_name,
    refresh_cookie_name,
    set_oauth_state_cookie,
    set_refresh_cookie,
)
from ..dependencies import Db, MailerDep, RedisDep, SettingsDep
from ..integrations.mail import Mailer
from ..models import OAuthAccount, User
from ..schemas import (
    AccessTokenResponse,
    LoginRequest,
    OAuthProvidersResponse,
    OtpChallengeResponse,
    OtpVerifyRequest,
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
oauth_router = APIRouter(prefix="/auth/oauth", tags=["auth"])

logger = logging.getLogger(__name__)
NO_STORE = {"Cache-Control": "no-store"}
PROVIDERS = ("google", "github")
# Свой state — 32 символа; ограничение отсекает мусор до сравнения.
MAX_STATE_LENGTH = 128


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


async def _send_code(
    db: AsyncSession,
    settings: Settings,
    mailer: Mailer,
    redis,
    email: str,
    purpose: str,
    password_hash: str | None = None,
) -> None:
    code = await otp.issue_code(db, settings, email, purpose, pending_password_hash=password_hash)
    await db.commit()
    subject, body = otp.format_message(code, purpose, settings.otp_ttl_seconds)
    await mailer.send(email, subject, body)
    if settings.otp_log_codes:
        # Только для локальной разработки: в production код в логах недопустим.
        logger.warning("OTP для %s (%s): %s", email, purpose, code)
    if settings.enable_testing_endpoints:
        # Тот же выключатель, что и у служебного роутера: без него код нигде
        # в открытом виде не сохраняется.
        from .testing import testing_otp_key

        await redis.set(
            testing_otp_key(settings.key_prefix, email, purpose),
            code,
            ex=settings.otp_ttl_seconds,
        )


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

    await _send_code(
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

    await _send_code(db, settings, mailer, redis, email, "login")
    return {"otp_required": True, "purpose": "login", "expires_in": settings.otp_ttl_seconds}


@router.post("/otp/verify", response_model=AccessTokenResponse)
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
async def logout(request: Request, response: Response, db: Db, settings: SettingsDep):
    """Отзывает сессию и удаляет cookie. Ответ одинаков независимо от результата."""
    presented = request.cookies.get(refresh_cookie_name(settings))
    await auth_service.logout_by_refresh(db, presented)
    clear_refresh_cookie(response, settings)


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
    tokens = await auth_service.start_session(db, redis, settings, user)

    redirect = RedirectResponse(f"{spa}/projects", status_code=303)
    set_refresh_cookie(redirect, settings, tokens.refresh_token)
    clear_oauth_state_cookie(redirect, settings)
    return redirect
