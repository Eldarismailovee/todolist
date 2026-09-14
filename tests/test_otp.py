"""Обязательное подтверждение входа и регистрации одноразовым кодом."""

from sqlalchemy import select, text, update

from app.config import get_settings
from app.cookies import session_cookie_name
from app.db import SessionLocal
from app.models import OtpCode, User
from app.otp import hash_code

from .conftest import OTP_CODE, login, plant_otp, register

settings = get_settings()
COOKIE = session_cookie_name(settings)
PASSWORD = "correct-horse-battery"


async def test_registration_requires_code_before_account_exists(client):
    started = await client.post(
        "/api/v1/auth/register", json={"email": "otp-new@example.com", "password": PASSWORD}
    )

    assert started.status_code == 202
    body = started.json()
    assert body["otp_required"] is True
    assert body["purpose"] == "register"
    # Пароль принят, но аккаунта ещё нет и сессия не выдана.
    assert not client.cookies.get(COOKIE)
    async with SessionLocal() as session:
        assert await session.scalar(select(User).where(User.email == "otp-new@example.com")) is None


async def test_registration_completes_only_with_correct_code(client):
    await client.post(
        "/api/v1/auth/register", json={"email": "otp-ok@example.com", "password": PASSWORD}
    )
    await plant_otp("otp-ok@example.com", "register")

    wrong = await client.post(
        "/api/v1/auth/otp/verify",
        json={"email": "otp-ok@example.com", "code": "000000", "purpose": "register"},
    )
    assert wrong.status_code == 401

    verified = await client.post(
        "/api/v1/auth/otp/verify",
        json={"email": "otp-ok@example.com", "code": OTP_CODE, "purpose": "register"},
    )
    assert verified.status_code == 200
    # Ответ — сам пользователь, а сессия приходит cookie: токенов клиенту нет.
    assert verified.json()["email"] == "otp-ok@example.com"
    assert client.cookies.get(COOKIE)


async def test_code_is_single_use(client):
    await register(client, "otp-once@example.com")
    # Тот же код второй раз не проходит: запись погашена.
    replay = await client.post(
        "/api/v1/auth/otp/verify",
        json={"email": "otp-once@example.com", "code": OTP_CODE, "purpose": "register"},
    )
    assert replay.status_code == 401


async def test_expired_code_rejected(client):
    await client.post(
        "/api/v1/auth/register", json={"email": "otp-old@example.com", "password": PASSWORD}
    )
    await plant_otp("otp-old@example.com", "register")
    async with SessionLocal() as session:
        await session.execute(text("UPDATE otp_codes SET expires_at = now() - interval '1 minute'"))
        await session.commit()

    response = await client.post(
        "/api/v1/auth/otp/verify",
        json={"email": "otp-old@example.com", "code": OTP_CODE, "purpose": "register"},
    )
    assert response.status_code == 401


async def test_attempts_are_limited(client):
    await client.post(
        "/api/v1/auth/register", json={"email": "otp-brute@example.com", "password": PASSWORD}
    )
    await plant_otp("otp-brute@example.com", "register")

    for _ in range(settings.otp_max_attempts):
        await client.post(
            "/api/v1/auth/otp/verify",
            json={"email": "otp-brute@example.com", "code": "111111", "purpose": "register"},
        )

    # После исчерпания попыток верный код уже не спасает.
    response = await client.post(
        "/api/v1/auth/otp/verify",
        json={"email": "otp-brute@example.com", "code": OTP_CODE, "purpose": "register"},
    )
    assert response.status_code == 401


async def test_login_requires_code_even_with_correct_password(client):
    await register(client, "otp-login@example.com")
    await client.post("/api/v1/auth/logout")

    started = await client.post(
        "/api/v1/auth/login", json={"email": "otp-login@example.com", "password": PASSWORD}
    )

    assert started.status_code == 202
    assert started.json()["purpose"] == "login"
    # Пароль верный, но пока код не введён, сессии нет.
    assert (await client.get("/api/v1/projects")).status_code == 401

    await plant_otp("otp-login@example.com", "login")
    verified = await client.post(
        "/api/v1/auth/otp/verify",
        json={"email": "otp-login@example.com", "code": OTP_CODE, "purpose": "login"},
    )
    assert verified.status_code == 200
    assert (await client.get("/api/v1/projects")).status_code == 200


async def test_login_code_does_not_work_for_registration(client):
    """Цель кода — часть подписи: код для входа не завершает регистрацию."""
    await register(client, "otp-purpose@example.com")
    await client.post(
        "/api/v1/auth/login",
        json={"email": "otp-purpose@example.com", "password": PASSWORD},
    )
    await plant_otp("otp-purpose@example.com", "login")

    response = await client.post(
        "/api/v1/auth/otp/verify",
        json={"email": "otp-purpose@example.com", "code": OTP_CODE, "purpose": "register"},
    )
    assert response.status_code == 401


async def test_code_is_not_stored_in_plaintext(client):
    await client.post(
        "/api/v1/auth/register", json={"email": "otp-hash@example.com", "password": PASSWORD}
    )
    async with SessionLocal() as session:
        record = await session.scalar(
            select(OtpCode).where(OtpCode.email == "otp-hash@example.com")
        )
        assert record is not None
        assert len(record.code_hash) == 64
        assert not record.code_hash.isdigit()

        # Подпись зависит от адреса: тот же код у другого адреса не совпадает.
        await session.execute(
            update(OtpCode)
            .where(OtpCode.id == record.id)
            .values(code_hash=hash_code(settings, "otp-hash@example.com", OTP_CODE))
        )
        await session.commit()
    assert hash_code(settings, "other@example.com", OTP_CODE) != record.code_hash


async def test_wrong_password_never_sends_a_code(client):
    await register(client, "otp-guard@example.com")

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "otp-guard@example.com", "password": "wrong-password-here"},
    )

    assert response.status_code == 401
    async with SessionLocal() as session:
        codes = await session.scalar(
            select(OtpCode.id)
            .where(OtpCode.email == "otp-guard@example.com", OtpCode.purpose == "login")
            .limit(1)
        )
    assert codes is None


async def test_login_helper_round_trip(client):
    """Полный цикл: регистрация, выход, повторный вход по коду."""
    await register(client, "otp-cycle@example.com")
    await client.post("/api/v1/auth/logout")

    await login(client, "otp-cycle@example.com")

    assert (await client.get("/api/v1/projects")).status_code == 200
