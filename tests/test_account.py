"""Аккаунт с паролем: признак has_password и подтверждение удаления."""

from sqlalchemy import select

from app.db import SessionLocal
from app.models import User

from .conftest import bearer, fresh_access, register

PASSWORD = "correct-horse-battery"


async def test_password_account_reports_that_it_has_a_password(client):
    await register(client, "with-password@example.com", PASSWORD)

    me = await client.get("/api/v1/user/me", headers=bearer(await fresh_access(client)))

    assert me.status_code == 200
    assert me.json()["has_password"] is True


async def test_deleting_a_password_account_requires_the_password(client):
    email = "delete-with-password@example.com"
    await register(client, email, PASSWORD)

    wrong = await client.request(
        "DELETE",
        "/api/v1/user/me",
        json={"password": "не тот пароль"},
        headers=bearer(await fresh_access(client)),
    )
    assert wrong.status_code == 403

    right = await client.request(
        "DELETE",
        "/api/v1/user/me",
        json={"password": PASSWORD},
        headers=bearer(await fresh_access(client)),
    )

    assert right.status_code == 204, right.text
    async with SessionLocal() as session:
        assert await session.scalar(select(User).where(User.email == email)) is None


async def test_delete_request_needs_exactly_one_proof(client):
    """Ни пустое подтверждение, ни оба сразу не считаются подтверждением."""
    await register(client, "delete-proof@example.com", PASSWORD)

    empty = await client.request(
        "DELETE", "/api/v1/user/me", json={}, headers=bearer(await fresh_access(client))
    )
    both = await client.request(
        "DELETE",
        "/api/v1/user/me",
        json={"password": PASSWORD, "code": "424242"},
        headers=bearer(await fresh_access(client)),
    )

    assert empty.status_code == 422
    assert both.status_code == 422
