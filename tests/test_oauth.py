"""Вход через внешнего провайдера.

Провайдер поднимается локально: адреса endpoint'ов вынесены в настройки именно
для этого, поэтому проверяется настоящий обмен кода, а не подменённый модуль.
"""

import asyncio
from urllib.parse import parse_qs, urlparse

import pytest
import uvicorn
from fastapi import FastAPI, Request
from sqlalchemy import select

from app.config import get_settings
from app.cookies import oauth_state_cookie_name, session_cookie_name
from app.db import SessionLocal
from app.models import OAuthAccount, User

from .conftest import OTP_CODE, plant_otp, register

settings = get_settings()
COOKIE = session_cookie_name(settings)
STATE_COOKIE = oauth_state_cookie_name(settings)
FAKE_PORT = 8101

# Профиль, который «вернёт» провайдер; тесты его подменяют.
STATE = {
    "id": "gh-1",
    "login": "octocat",
    "name": "Octo Cat",
    "email": "octo@example.com",
    "verified": True,
    "seen_code_verifier": None,
}


def build_fake_provider() -> FastAPI:
    app = FastAPI()

    @app.post("/token")
    async def token(request: Request):
        form = await request.form()
        STATE["seen_code_verifier"] = form.get("code_verifier")
        if form.get("code") != "good-code":
            return {"error": "invalid_grant"}
        return {"access_token": "provider-token", "token_type": "bearer"}

    @app.get("/emails")
    async def emails():
        # Именно этот список подтверждает владение адресом у GitHub.
        return [{"email": STATE["email"], "primary": True, "verified": STATE["verified"]}]

    @app.get("/userinfo")
    async def userinfo():
        return {
            "id": STATE["id"],
            "sub": STATE["id"],
            "login": STATE["login"],
            "name": STATE["name"],
            "email": STATE["email"],
            "email_verified": True,
        }

    return app


@pytest.fixture
async def fake_provider():
    # Фикстура на тест: у session-scope был бы свой event loop, отличный от
    # цикла теста, и pytest-asyncio это запрещает.
    config = uvicorn.Config(
        build_fake_provider(), host="127.0.0.1", port=FAKE_PORT, log_level="warning"
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.05)
    yield f"http://127.0.0.1:{FAKE_PORT}"
    server.should_exit = True
    await task


@pytest.fixture
async def oauth_client(fake_provider, monkeypatch):
    """Приложение поднимается в процессе теста: подменённые адреса провайдера
    видны только здесь, отдельным воркерам их не передать."""
    monkeypatch.setattr(settings, "github_client_id", "test-client")
    monkeypatch.setattr(settings, "github_client_secret", "test-secret")
    monkeypatch.setattr(settings, "github_token_url", f"{fake_provider}/token")
    monkeypatch.setattr(settings, "github_userinfo_url", f"{fake_provider}/userinfo")
    monkeypatch.setattr(settings, "github_emails_url", f"{fake_provider}/emails")
    monkeypatch.setattr(settings, "github_authorize_url", f"{fake_provider}/authorize")
    monkeypatch.setattr(settings, "oauth_redirect_base", "http://testserver")

    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Origin": "http://testserver", "X-CSRF-Guard": "1"},
            timeout=30.0,
            follow_redirects=False,
        ) as http:
            yield http


async def _authorize(http) -> str:
    """Проходит шаг авторизации и возвращает state из ссылки провайдера."""
    start = await http.get("/api/v1/auth/oauth/github/start")
    assert start.status_code == 307
    query = parse_qs(urlparse(start.headers["location"]).query)
    return query["state"][0]


async def test_providers_list_reflects_configuration(client):
    """Без client_id провайдер выключен и кнопка не показывается."""
    response = await client.get("/api/v1/auth/oauth/providers")
    assert response.status_code == 200
    assert response.json() == {"providers": []}


async def test_start_redirects_with_state(oauth_client):
    start = await oauth_client.get("/api/v1/auth/oauth/github/start")

    assert start.status_code == 307
    location = urlparse(start.headers["location"])
    query = parse_qs(location.query)
    assert query["client_id"] == ["test-client"]
    assert query["redirect_uri"] == ["http://testserver/api/v1/auth/oauth/github/callback"]
    assert len(query["state"][0]) > 20


async def test_callback_creates_user_and_session(oauth_client):
    STATE["id"] = "gh-new"
    STATE["email"] = "gh-new@example.com"
    state = await _authorize(oauth_client)

    callback = await oauth_client.get(
        f"/api/v1/auth/oauth/github/callback?code=good-code&state={state}"
    )

    assert callback.status_code == 303
    assert callback.headers["location"] == "http://testserver/projects"
    assert COOKIE in callback.headers.get("set-cookie", "")

    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.email == "gh-new@example.com"))
        assert user is not None
        # У пользователя из OAuth пароля нет вовсе.
        assert user.hashed_password is None
        account = await session.scalar(
            select(OAuthAccount).where(OAuthAccount.provider_account_id == "gh-new")
        )
        assert account is not None and account.user_id == user.id


async def test_second_login_reuses_the_same_account(oauth_client):
    STATE["id"] = "gh-repeat"
    STATE["email"] = "gh-repeat@example.com"

    for _ in range(2):
        state = await _authorize(oauth_client)
        response = await oauth_client.get(
            f"/api/v1/auth/oauth/github/callback?code=good-code&state={state}"
        )
        assert response.status_code == 303

    async with SessionLocal() as session:
        users = (
            await session.scalars(select(User).where(User.email == "gh-repeat@example.com"))
        ).all()
        accounts = (
            await session.scalars(
                select(OAuthAccount).where(OAuthAccount.provider_account_id == "gh-repeat")
            )
        ).all()
    assert len(users) == 1
    assert len(accounts) == 1


async def test_state_cannot_be_replayed(oauth_client):
    STATE["id"] = "gh-replay"
    STATE["email"] = "gh-replay@example.com"
    state = await _authorize(oauth_client)

    first = await oauth_client.get(
        f"/api/v1/auth/oauth/github/callback?code=good-code&state={state}"
    )
    # Cookie на успехе гасится, поэтому для повтора она подставляется заново:
    # иначе проверялась бы привязка к браузеру, а не одноразовость state.
    oauth_client.cookies.set(STATE_COOKIE, state)
    replay = await oauth_client.get(
        f"/api/v1/auth/oauth/github/callback?code=good-code&state={state}"
    )

    assert first.status_code == 303 and first.headers["location"].endswith("/projects")
    # Повтор ссылки-возврата не создаёт вторую сессию.
    assert replay.headers["location"].endswith("/login?oauth_error=1")


async def test_start_binds_state_to_the_browser(oauth_client):
    """Cookie перехода несёт тот же state, что ушёл провайдеру."""
    start = await oauth_client.get("/api/v1/auth/oauth/github/start")

    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    assert oauth_client.cookies[STATE_COOKIE] == state
    header = start.headers["set-cookie"]
    assert "HttpOnly" in header and "SameSite=lax" in header


async def test_callback_rejects_state_from_another_browser(oauth_client):
    """Перенос незавершённой ссылки-возврата в чужой браузер не даёт сессии.

    Иначе жертва, открыв ссылку, молча оказывалась бы в аккаунте атакующего.
    """
    STATE["id"] = "gh-transfer"
    STATE["email"] = "gh-transfer@example.com"
    state = await _authorize(oauth_client)

    from httpx import ASGITransport, AsyncClient

    from app.main import app

    # Второй браузер: те же адреса и то же приложение, но своя банка cookie.
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
    ) as victim:
        response = await victim.get(
            f"/api/v1/auth/oauth/github/callback?code=good-code&state={state}"
        )

    assert response.headers["location"].endswith("/login?oauth_error=1")
    assert COOKIE not in response.cookies
    async with SessionLocal() as session:
        assert (
            await session.scalar(select(User).where(User.email == "gh-transfer@example.com"))
            is None
        )

    # State не погашен: свой браузер доводит начатый вход до конца.
    ours = await oauth_client.get(
        f"/api/v1/auth/oauth/github/callback?code=good-code&state={state}"
    )
    assert ours.headers["location"].endswith("/projects")


async def test_successful_callback_clears_the_transition_cookie(oauth_client):
    STATE["id"] = "gh-cleanup"
    STATE["email"] = "gh-cleanup@example.com"
    state = await _authorize(oauth_client)

    response = await oauth_client.get(
        f"/api/v1/auth/oauth/github/callback?code=good-code&state={state}"
    )

    assert response.headers["location"].endswith("/projects")
    assert not oauth_client.cookies.get(STATE_COOKIE)


async def test_failed_callback_clears_the_transition_cookie(oauth_client):
    state = await _authorize(oauth_client)

    response = await oauth_client.get(
        f"/api/v1/auth/oauth/github/callback?code=bad-code&state={state}"
    )

    assert response.headers["location"].endswith("/login?oauth_error=1")
    assert not oauth_client.cookies.get(STATE_COOKIE)


async def test_unknown_state_is_rejected(oauth_client):
    response = await oauth_client.get(
        "/api/v1/auth/oauth/github/callback?code=good-code&state=made-up"
    )
    assert response.headers["location"].endswith("/login?oauth_error=1")


async def test_provider_error_redirects_to_login(oauth_client):
    state = await _authorize(oauth_client)
    response = await oauth_client.get(
        f"/api/v1/auth/oauth/github/callback?code=bad-code&state={state}"
    )
    assert response.headers["location"].endswith("/login?oauth_error=1")


async def test_oauth_links_to_existing_account_by_verified_email(oauth_client):
    """Существующий аккаунт связывается, а не дублируется."""
    await register(oauth_client, "linked@example.com")
    await oauth_client.post("/api/v1/auth/logout")

    STATE["id"] = "gh-link"
    STATE["email"] = "linked@example.com"
    STATE["verified"] = True
    state = await _authorize(oauth_client)
    response = await oauth_client.get(
        f"/api/v1/auth/oauth/github/callback?code=good-code&state={state}"
    )

    assert response.headers["location"].endswith("/projects")
    async with SessionLocal() as session:
        users = (
            await session.scalars(select(User).where(User.email == "linked@example.com"))
        ).all()
        account = await session.scalar(
            select(OAuthAccount).where(OAuthAccount.provider_account_id == "gh-link")
        )
    assert len(users) == 1
    # Внешний аккаунт привязан к уже существующему пользователю.
    assert account is not None and account.user_id == users[0].id


async def test_unverified_email_does_not_link_existing_account(oauth_client):
    """Неподтверждённый адрес не должен давать доступ к чужому аккаунту."""
    await register(oauth_client, "victim@example.com")
    await oauth_client.post("/api/v1/auth/logout")

    STATE["id"] = "gh-attacker"
    STATE["email"] = "victim@example.com"
    STATE["verified"] = False
    state = await _authorize(oauth_client)
    response = await oauth_client.get(
        f"/api/v1/auth/oauth/github/callback?code=good-code&state={state}"
    )
    STATE["verified"] = True

    assert response.headers["location"].endswith("/login?oauth_error=1")
    async with SessionLocal() as session:
        account = await session.scalar(
            select(OAuthAccount).where(OAuthAccount.provider_account_id == "gh-attacker")
        )
    assert account is None


async def test_session_from_oauth_works_for_api(oauth_client):
    STATE["id"] = "gh-api"
    STATE["email"] = "gh-api@example.com"
    state = await _authorize(oauth_client)
    await oauth_client.get(f"/api/v1/auth/oauth/github/callback?code=good-code&state={state}")

    me = await oauth_client.get("/api/v1/user/me")
    assert me.status_code == 200
    assert me.json()["email"] == "gh-api@example.com"


async def _sign_in_with_github(oauth_client, account_id: str, email: str) -> None:
    STATE["id"] = account_id
    STATE["email"] = email
    state = await _authorize(oauth_client)
    callback = await oauth_client.get(
        f"/api/v1/auth/oauth/github/callback?code=good-code&state={state}"
    )
    assert callback.status_code == 303


async def test_oauth_account_reports_that_it_has_no_password(oauth_client):
    """has_password брался из значения по умолчанию и всегда был true."""
    await _sign_in_with_github(oauth_client, "gh-nopass", "gh-nopass@example.com")

    me = await oauth_client.get("/api/v1/user/me")

    assert me.status_code == 200
    assert me.json()["has_password"] is False


async def test_oauth_account_is_deleted_after_email_confirmation(oauth_client):
    """Пароля у такого аккаунта нет, и удалить его иначе было невозможно."""
    email = "gh-delete@example.com"
    await _sign_in_with_github(oauth_client, "gh-delete", email)

    # Пароль подтверждением быть не может: его просто нет.
    refused = await oauth_client.request(
        "DELETE",
        "/api/v1/user/me",
        json={"password": "любой-пароль"},
    )
    assert refused.status_code == 403

    requested = await oauth_client.post("/api/v1/user/delete-code")
    assert requested.status_code == 202, requested.text
    assert requested.json()["purpose"] == "delete_account"
    await plant_otp(email, "delete_account")

    deleted = await oauth_client.request(
        "DELETE",
        "/api/v1/user/me",
        json={"code": OTP_CODE},
    )

    assert deleted.status_code == 204, deleted.text
    async with SessionLocal() as session:
        assert await session.scalar(select(User).where(User.email == email)) is None


async def test_delete_code_is_not_accepted_as_a_login(oauth_client):
    """Код привязан к действию: сессию по нему получить нельзя."""
    email = "gh-actionbound@example.com"
    await _sign_in_with_github(oauth_client, "gh-actionbound", email)
    await oauth_client.post("/api/v1/user/delete-code")
    await plant_otp(email, "delete_account")

    # Цель delete_account не входит в допустимые значения /auth/otp/verify.
    response = await oauth_client.post(
        "/api/v1/auth/otp/verify",
        json={"email": email, "code": OTP_CODE, "purpose": "delete_account"},
    )
    assert response.status_code == 422

    # Тем же кодом нельзя войти и под видом обычного входа.
    as_login = await oauth_client.post(
        "/api/v1/auth/otp/verify",
        json={"email": email, "code": OTP_CODE, "purpose": "login"},
    )
    assert as_login.status_code == 401


async def test_wrong_delete_code_does_not_delete_the_account(oauth_client):
    email = "gh-badcode@example.com"
    await _sign_in_with_github(oauth_client, "gh-badcode", email)
    await oauth_client.post("/api/v1/user/delete-code")
    await plant_otp(email, "delete_account")

    response = await oauth_client.request(
        "DELETE",
        "/api/v1/user/me",
        json={"code": "000000"},
    )

    assert response.status_code == 403
    async with SessionLocal() as session:
        assert await session.scalar(select(User).where(User.email == email)) is not None
