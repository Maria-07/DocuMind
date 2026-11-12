"""document links

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-12 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Hyperlinks extracted from documents (PDF link annotations, Word hyperlinks, Excel cell links and
    # URLs written as text), one row per URL and location, versioned like document_chunks.
    op.create_table(
        "document_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("index_version", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=True),
        sa.Column("link_type", sa.String(length=16), nullable=False),
        sa.Column("anchor_text", sa.Text(), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("sheet_name", sa.String(length=255), nullable=True),
        sa.Column("cell_reference", sa.String(length=32), nullable=True),
        sa.Column("section_name", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], name=op.f("fk_document_links_document_id_documents"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_links")),
        sa.UniqueConstraint("document_id", "index_version", "position", name="uq_document_links_position"),
    )
    op.create_index("ix_document_links_document_version", "document_links", ["document_id", "index_version"])
    op.create_index("ix_document_links_org_domain", "document_links", ["organization_id", "domain"])


def downgrade() -> None:
    op.drop_index("ix_document_links_org_domain", table_name="document_links")
    op.drop_index("ix_document_links_document_version", table_name="document_links")
    op.drop_table("document_links")
