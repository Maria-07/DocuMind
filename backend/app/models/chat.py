from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ARRAY, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Conversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A chat thread. Owner-only: not even admins read other users' conversations."""

    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_user_updated", "user_id", "updated_at"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # Optional retrieval scope: {"document_ids": [...], "category_ids": [...], "file_types": [...]}
    scope: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Message(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conversation_created", "conversation_id", "created_at"),)

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # streaming | complete | error | cancelled
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="complete")
    # True when the answer is grounded in retrieved documents (has at least one valid citation).
    grounded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    search_query: Mapped[str | None] = mapped_column(Text)  # standalone query used for retrieval
    # How the assistant handled the message: "chat" (no documents needed) or "documents" (retrieval +
    # citations). NULL = created before routing existed, i.e. always a document answer.
    route: Mapped[str | None] = mapped_column(String(16))
    # Set when the user edited an earlier message and resubmitted: this message no longer belongs to the live
    # conversation (it is hidden from history and context) but is kept for the record.
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # On a user message that replaced an edited one: the id of the message it replaces (no FK: it may be old).
    edit_of: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    model: Mapped[str | None] = mapped_column(String(100))
    usage: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)


class MessageCitation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A citation shown with an answer. Snapshot fields keep history readable after deletions."""

    __tablename__ = "message_citations"
    __table_args__ = (Index("ix_message_citations_message", "message_id", "citation_index"),)

    message_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False)
    citation_index: Mapped[int] = mapped_column(Integer, nullable=False)  # the [n] shown in the answer
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # no FK: chunks are replaced on re-index
    document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("documents.id", ondelete="SET NULL"))
    document_title: Mapped[str] = mapped_column(String(300), nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    sheet_name: Mapped[str | None] = mapped_column(String(255))
    row_start: Mapped[int | None] = mapped_column(Integer)
    row_end: Mapped[int | None] = mapped_column(Integer)
    section_path: Mapped[str | None] = mapped_column(String(1000))
    cited_text: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float | None] = mapped_column(Float)


class RetrievalLog(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One retrieval (chat turn or search). Query text is not stored here; see messages."""

    __tablename__ = "retrieval_logs"
    __table_args__ = (Index("ix_retrieval_logs_org_created", "organization_id", "created_at"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    message_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("messages.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # chat | search
    mode: Mapped[str] = mapped_column(String(16), nullable=False)  # hybrid | semantic
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    filtered_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # removed by the PG post-check
    returned_chunk_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid), nullable=False, default=list)
    top_score: Mapped[float | None] = mapped_column(Float)
    no_answer: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    latency: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    embedding_config_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("embedding_configs.id", ondelete="SET NULL")
    )
