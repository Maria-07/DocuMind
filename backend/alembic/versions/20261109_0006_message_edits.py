"""message edits

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-09 23:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Editing a message hides it and everything after it (superseded_at) instead of deleting it.
    op.add_column("messages", sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("messages", sa.Column("edit_of", sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "edit_of")
    op.drop_column("messages", "superseded_at")
