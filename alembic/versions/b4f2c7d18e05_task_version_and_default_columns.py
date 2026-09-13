"""task version column and backfill of default board columns

Revision ID: b4f2c7d18e05
Revises: ce1a71b457ae
Create Date: 2026-09-13 00:20:00.000000

Версия задачи нужна для условной записи: без неё поздний PATCH молча
перезаписывал более новое изменение из другой вкладки.

Колонки по умолчанию раньше создавались при первом чтении доски. Чтение
перестало писать, поэтому проектам без колонок набор создаётся здесь — иначе
у старого проекта доска осталась бы пустой навсегда.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4f2c7d18e05"
down_revision: str | None = "ce1a71b457ae"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )

    # Один INSERT на все три колонки: по одной за раз первый же вставленный
    # ряд перестал бы удовлетворять условию «у проекта нет колонок», и проект
    # получил бы только одну колонку. Значения повторяют
    # app.board_service.DEFAULT_COLUMNS намеренно: миграция описывает состояние
    # схемы на своей ревизии и не должна меняться вместе с кодом.
    op.execute(
        sa.text(
            """
            INSERT INTO board_columns (project_id, title, position, is_done_column)
            SELECT p.id, d.title, d.position, d.is_done
            FROM projects p
            CROSS JOIN (
                VALUES
                    ('К выполнению', 1024.0, false),
                    ('В работе', 2048.0, false),
                    ('Готово', 3072.0, true)
            ) AS d(title, position, is_done)
            WHERE NOT EXISTS (
                SELECT 1 FROM board_columns c WHERE c.project_id = p.id
            )
            """
        )
    )


def downgrade() -> None:
    op.drop_column("tasks", "version")
