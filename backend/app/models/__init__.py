"""Import every model so Base.metadata is complete (Alembic, tests)."""

from app.models.audit import AuditLog
from app.models.chunk import DocumentChunk, EmbeddingConfig
from app.models.document import Category, Document, DocumentPermission, IngestionJob
from app.models.organization import Organization
from app.models.user import GroupMember, RefreshToken, User, UserGroup

__all__ = [
    "AuditLog",
    "Category",
    "Document",
    "DocumentChunk",
    "DocumentPermission",
    "EmbeddingConfig",
    "GroupMember",
    "IngestionJob",
    "Organization",
    "RefreshToken",
    "User",
    "UserGroup",
]
