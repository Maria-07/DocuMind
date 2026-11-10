"""message route

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-09 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable: existing rows predate routing and were all document answers (NULL is read as "documents").
    op.add_column("messages", sa.Column("route", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "route")
