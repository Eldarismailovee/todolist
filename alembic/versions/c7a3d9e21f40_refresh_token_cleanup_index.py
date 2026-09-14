"""index for refresh token retention cleanup

Revision ID: c7a3d9e21f40
Revises: b4f2c7d18e05
Create Date: 2026-09-13 01:10:00.000000

Уборка удаляет записи по `expires_at`. Без индекса это последовательное чтение
таблицы, которая растёт на каждый защищённый запрос клиента.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c7a3d9e21f40"
down_revision: str | None = "b4f2c7d18e05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_refresh_tokens_expires_at", "refresh_tokens", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_refresh_tokens_expires_at", table_name="refresh_tokens")
