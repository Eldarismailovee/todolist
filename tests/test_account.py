"""Аккаунт с паролем: признак has_password и подтверждение удаления."""

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import User

from .conftest import register

PASSWORD = "correct-horse-battery"


async def test_password_account_reports_that_it_has_a_password(client):
    await register(client, "with-password@example.com", PASSWORD)

    me = await client.get("/api/v1/user/me")

    assert me.status_code == 200
    assert me.json()["has_password"] is True


async def test_deleting_a_password_account_requires_the_password(client):
    email = "delete-with-password@example.com"
    await register(client, email, PASSWORD)

    wrong = await client.request(
        "DELETE",
        "/api/v1/user/me",
        json={"password": "не тот пароль"},
    )
    assert wrong.status_code == 403

    right = await client.request(
        "DELETE",
        "/api/v1/user/me",
        json={"password": PASSWORD},
    )

    assert right.status_code == 204, right.text
    async with SessionLocal() as session:
        assert await session.scalar(select(User).where(User.email == email)) is None


async def test_password_checks_are_rate_limited_per_user(client):
    """Перебор текущего пароля идёт из авторизованной сессии.

    Общий лимит refresh по IP такие попытки не считает вовсе.
    """
    await register(client, "brute-force@example.com", PASSWORD)
    settings = get_settings()

    codes = []
    for _ in range(settings.login_rate_limit + 2):
        response = await client.post(
            "/api/v1/user/change-password",
            json={"current_password": "не тот пароль", "new_password": "длинный-новый-пароль"},
        )
        codes.append(response.status_code)

    assert 403 in codes
    assert codes[-1] == 429


async def test_delete_request_needs_exactly_one_proof(client):
    """Ни пустое подтверждение, ни оба сразу не считаются подтверждением."""
    await register(client, "delete-proof@example.com", PASSWORD)

    empty = await client.request("DELETE", "/api/v1/user/me", json={})
    both = await client.request(
        "DELETE",
        "/api/v1/user/me",
        json={"password": PASSWORD, "code": "424242"},
    )

    assert empty.status_code == 422
    assert both.status_code == 422
