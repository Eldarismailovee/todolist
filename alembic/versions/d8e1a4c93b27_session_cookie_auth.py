"""session cookie auth: session token on auth_sessions, refresh_tokens dropped

Revision ID: d8e1a4c93b27
Revises: c7a3d9e21f40
Create Date: 2026-09-14 00:40:00.000000

Обмен refresh на одноразовый access перед каждым запросом заменён сессионной
cookie: сессия сама предъявляется клиентом, поэтому у неё появились хеш
значения и предел простоя, а таблица refresh_tokens стала не нужна.

Действующие входы не переносятся: старые значения не подходят к новой схеме,
и все сессии придётся начать заново. Это осознанная часть смены протокола.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8e1a4c93b27"
down_revision: str | None = "c7a3d9e21f40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Существующие сессии не могут получить значение cookie задним числом:
    # они удаляются, а не размечаются пустым хешем.
    op.execute(sa.text("DELETE FROM auth_sessions"))

    op.add_column("auth_sessions", sa.Column("token_hash", sa.String(length=64), nullable=False))
    op.add_column(
        "auth_sessions",
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_auth_sessions_token_hash", "auth_sessions", ["token_hash"], unique=True
    )
    op.create_index("ix_auth_sessions_idle_expires_at", "auth_sessions", ["idle_expires_at"])

    op.drop_table("refresh_tokens")


def downgrade() -> None:
    op.create_table(
        "refresh_tokens",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["auth_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_index("ix_refresh_tokens_session_id", "refresh_tokens", ["session_id"])
    op.create_index("ix_refresh_tokens_expires_at", "refresh_tokens", ["expires_at"])

    op.drop_index("ix_auth_sessions_idle_expires_at", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_token_hash", table_name="auth_sessions")
    op.drop_column("auth_sessions", "idle_expires_at")
    op.drop_column("auth_sessions", "token_hash")
