"""Initial schema: tenancy, identity, documents, permissions, ingestion jobs, audit log.

Revision ID: 0001
Revises:
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(name: str, *values: str) -> sa.Enum:
    # Frozen copy of app.db.base.str_enum: VARCHAR(32) + CHECK constraint ck_<table>_<name>.
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, length=32)


def _ts(name: str, nullable: bool = True, default_now: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.func.now() if default_now else None,
    )


def _jsonb(name: str) -> sa.Column:
    return sa.Column(name, postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb"))


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        _jsonb("settings"),
        sa.Column("storage_quota_bytes", sa.BigInteger(), nullable=True),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
        sa.UniqueConstraint("slug", name="uq_organizations_slug"),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE", name="fk_users_organization_id_organizations"),
            nullable=False,
        ),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("role", _enum("user_role", "admin", "manager", "user"), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("failed_login_attempts", sa.Integer(), nullable=False),
        _ts("locked_until"),
        _ts("last_login_at"),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_organization_id_role", "users", ["organization_id", "role"])

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_refresh_tokens_user_id_users"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        _ts("expires_at", nullable=False),
        _ts("revoked_at"),
        sa.Column(
            "replaced_by_id",
            sa.Uuid(),
            sa.ForeignKey(
                "refresh_tokens.id", ondelete="SET NULL", name="fk_refresh_tokens_replaced_by_id_refresh_tokens"
            ),
            nullable=True,
        ),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
        _ts("created_at", nullable=False, default_now=True),
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"])

    op.create_table(
        "user_groups",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE", name="fk_user_groups_organization_id_organizations"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
        sa.UniqueConstraint("organization_id", "name", name="uq_user_groups_org_name"),
    )

    op.create_table(
        "group_members",
        sa.Column(
            "group_id",
            sa.Uuid(),
            sa.ForeignKey("user_groups.id", ondelete="CASCADE", name="fk_group_members_group_id_user_groups"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_group_members_user_id_users"),
            primary_key=True,
        ),
        _ts("created_at", nullable=False, default_now=True),
    )
    op.create_index("ix_group_members_user_id", "group_members", ["user_id"])

    op.create_table(
        "categories",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE", name="fk_categories_organization_id_organizations"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("color", sa.String(7), nullable=True),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
        sa.UniqueConstraint("organization_id", "name", name="uq_categories_org_name"),
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE", name="fk_documents_organization_id_organizations"),
            nullable=False,
        ),
        sa.Column(
            "owner_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_documents_owner_id_users"),
            nullable=True,
        ),
        sa.Column(
            "category_id",
            sa.Uuid(),
            sa.ForeignKey("categories.id", ondelete="SET NULL", name="fk_documents_category_id_categories"),
            nullable=True,
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("file_type", _enum("file_type", "pdf", "docx", "xlsx", "xls"), nullable=False),
        sa.Column("mime_type", sa.String(120), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column(
            "status",
            _enum("document_status", "uploaded", "processing", "indexed", "failed"),
            nullable=False,
        ),
        sa.Column(
            "visibility",
            _enum("document_visibility", "private", "organization", "restricted"),
            nullable=False,
        ),
        sa.Column("active_index_version", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("sheet_names", postgresql.ARRAY(sa.String(255)), nullable=True),
        _jsonb("metadata"),
        sa.Column("error_message", sa.Text(), nullable=True),
        _ts("indexed_at"),
        _ts("deleted_at"),
        _ts("purged_at"),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
    )
    op.create_index(
        "uq_documents_org_sha256_active",
        "documents",
        ["organization_id", "sha256"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_documents_org_status", "documents", ["organization_id", "status"])
    op.create_index("ix_documents_org_created_at", "documents", ["organization_id", "created_at"])
    op.create_index("ix_documents_org_category", "documents", ["organization_id", "category_id"])

    op.create_table(
        "document_permissions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("documents.id", ondelete="CASCADE", name="fk_document_permissions_document_id_documents"),
            nullable=False,
        ),
        sa.Column("principal_type", _enum("principal_type", "user", "group"), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("permission", _enum("grant_permission", "view", "manage"), nullable=False),
        sa.Column(
            "granted_by",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_document_permissions_granted_by_users"),
            nullable=True,
        ),
        _ts("created_at", nullable=False, default_now=True),
        sa.UniqueConstraint("document_id", "principal_type", "principal_id", name="uq_document_permissions_principal"),
    )
    op.create_index("ix_document_permissions_principal", "document_permissions", ["principal_type", "principal_id"])

    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("documents.id", ondelete="CASCADE", name="fk_ingestion_jobs_document_id_documents"),
            nullable=False,
        ),
        sa.Column("index_version", sa.Integer(), nullable=True),
        sa.Column("status", _enum("job_status", "queued", "running", "succeeded", "failed"), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(32), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        _jsonb("metrics"),
        sa.Column(
            "requested_by",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_ingestion_jobs_requested_by_users"),
            nullable=True,
        ),
        _ts("started_at"),
        _ts("finished_at"),
        _ts("created_at", nullable=False, default_now=True),
    )
    op.create_index("ix_ingestion_jobs_document_created", "ingestion_jobs", ["document_id", "created_at"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE", name="fk_audit_logs_organization_id_organizations"),
            nullable=True,
        ),
        sa.Column(
            "actor_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_audit_logs_actor_id_users"),
            nullable=True,
        ),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=True),
        sa.Column("resource_id", sa.String(64), nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        _jsonb("details"),
        _ts("created_at", nullable=False, default_now=True),
    )
    op.create_index("ix_audit_logs_org_created_at", "audit_logs", ["organization_id", "created_at"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])


def downgrade() -> None:
    for table in (
        "audit_logs",
        "ingestion_jobs",
        "document_permissions",
        "documents",
        "categories",
        "group_members",
        "user_groups",
        "refresh_tokens",
        "users",
        "organizations",
    ):
        op.drop_table(table)
