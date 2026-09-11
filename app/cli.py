"""Служебные команды: первый администратор и наполнение справочника атрибутов.

uv run python -m app.cli create-admin admin@example.com 'длинный-пароль'
uv run python -m app.cli grant-admin admin@example.com
uv run python -m app.cli seed-attributes
"""

import argparse
import asyncio
import sys

from sqlalchemy import select

from .config import get_settings
from .db import SessionLocal, engine
from .models import TaskAttributeMeta, User
from .security import hash_password

# Справочник описывает ДОПОЛНИТЕЛЬНЫЕ поля. Заголовок, срок и признак
# выполнения — обычные колонки задачи, дублировать их здесь нельзя: обязательный
# атрибут с таким же смыслом ломал бы создание задачи из интерфейса.
DEFAULT_ATTRIBUTES = [
    {"code": "priority", "title": "Приоритет", "type": "string", "is_required": False},
    {"code": "billable", "title": "Оплачиваемая", "type": "boolean", "is_required": False},
    {"code": "reminder_date", "title": "Дата напоминания", "type": "date", "is_required": False},
]


async def create_admin(email: str, password: str) -> None:
    settings = get_settings()
    if len(password) < settings.password_min_length:
        sys.exit(f"Пароль должен быть не короче {settings.password_min_length} символов")

    async with SessionLocal() as db:
        if await db.scalar(select(User).where(User.email == email.lower())):
            sys.exit("Пользователь с таким email уже существует")
        db.add(
            User(
                email=email.lower(),
                hashed_password=await hash_password(password),
                is_admin=True,
            )
        )
        await db.commit()
    print(f"Администратор {email} создан")


async def grant_admin(email: str) -> None:
    async with SessionLocal() as db:
        user = await db.scalar(select(User).where(User.email == email.lower()))
        if user is None:
            sys.exit("Пользователь не найден")
        user.is_admin = True
        await db.commit()
    print(f"{email} получил права администратора")


async def seed_attributes() -> None:
    async with SessionLocal() as db:
        for row in DEFAULT_ATTRIBUTES:
            if await db.get(TaskAttributeMeta, row["code"]) is None:
                db.add(TaskAttributeMeta(**row))
        await db.commit()
    print("Справочник атрибутов заполнен")


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create-admin", help="создать администратора")
    create.add_argument("email")
    create.add_argument("password")

    grant = commands.add_parser("grant-admin", help="выдать права существующему пользователю")
    grant.add_argument("email")

    commands.add_parser("seed-attributes", help="заполнить справочник атрибутов примером")

    args = parser.parse_args()

    async def run() -> None:
        try:
            if args.command == "create-admin":
                await create_admin(args.email, args.password)
            elif args.command == "grant-admin":
                await grant_admin(args.email)
            else:
                await seed_attributes()
        finally:
            await engine.dispose()

    asyncio.run(run())


if __name__ == "__main__":
    main()
