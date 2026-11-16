"""workflow automation (n8n)

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-16 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = ("uploaded", "processing", "indexed", "failed")
_NEW = ("uploaded", "queued", "processing", "retrying", "indexed", "failed")
_CONSTRAINT = "document_status"  # the naming convention makes it ck_documents_document_status


def _values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    # Document statuses 'queued' and 'retrying' (only used for jobs driven by the n8n workflow).
    op.drop_constraint(_CONSTRAINT, "documents", type_="check")
    op.create_check_constraint(_CONSTRAINT, "documents", f"status IN ({_values(_NEW)})")

    # Workflow bookkeeping on the ingestion job. PostgreSQL stays the source of truth for status; these
    # columns only record what the orchestrator needs to decide (retry or give up) and what to show.
    op.add_column(
        "ingestion_jobs", sa.Column("workflow_managed", sa.Boolean(), server_default=sa.text("false"), nullable=False)
    )
    op.add_column("ingestion_jobs", sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False))
    op.add_column(
        "ingestion_jobs", sa.Column("retryable", sa.Boolean(), server_default=sa.text("false"), nullable=False)
    )
    op.add_column("ingestion_jobs", sa.Column("error_code", sa.String(length=64), nullable=True))
    op.add_column("ingestion_jobs", sa.Column("workflow_status", sa.String(length=24), nullable=True))
    op.add_column("ingestion_jobs", sa.Column("workflow_execution_id", sa.String(length=100), nullable=True))
    op.add_column("ingestion_jobs", sa.Column("workflow_updated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for column in (
        "workflow_updated_at",
        "workflow_execution_id",
        "workflow_status",
        "error_code",
        "retryable",
        "retry_count",
        "workflow_managed",
    ):
        op.drop_column("ingestion_jobs", column)
    # Documents in the new statuses have no equivalent in the old model: map them back before restoring the check.
    op.execute("UPDATE documents SET status = 'uploaded' WHERE status = 'queued'")
    op.execute("UPDATE documents SET status = 'processing' WHERE status = 'retrying'")
    op.drop_constraint(_CONSTRAINT, "documents", type_="check")
    op.create_check_constraint(_CONSTRAINT, "documents", f"status IN ({_values(_OLD)})")
