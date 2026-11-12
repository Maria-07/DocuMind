"""Import every model so Base.metadata is complete (Alembic, tests)."""

from app.models.audit import AuditLog
from app.models.chat import Conversation, Message, MessageCitation, RetrievalLog
from app.models.chunk import DocumentChunk, EmbeddingConfig
from app.models.document import Category, Document, DocumentPermission, IngestionJob
from app.models.evaluation import DocumentComparison, EvalCase, EvalCaseResult, EvalDataset, EvalRun
from app.models.link import DocumentLink
from app.models.organization import Organization
from app.models.user import GroupMember, RefreshToken, User, UserGroup

__all__ = [
    "AuditLog",
    "Category",
    "Conversation",
    "Document",
    "DocumentChunk",
    "DocumentComparison",
    "DocumentLink",
    "DocumentPermission",
    "EmbeddingConfig",
    "EvalCase",
    "EvalCaseResult",
    "EvalDataset",
    "EvalRun",
    "GroupMember",
    "IngestionJob",
    "Message",
    "MessageCitation",
    "Organization",
    "RefreshToken",
    "RetrievalLog",
    "User",
    "UserGroup",
]
