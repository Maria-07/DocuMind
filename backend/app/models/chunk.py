from __future__ import annotations

import uuid

from sqlalchemy import ARRAY, Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class EmbeddingConfig(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One embedding + chunking configuration ↔ one Qdrant collection.

    Changing the model or chunking parameters creates a new row/collection, so the old index keeps
    serving until documents are re-indexed — and evaluation runs can compare configurations.
    """

    __tablename__ = "embedding_configs"
    __table_args__ = (
        Index("uq_embedding_configs_single_active", "is_active", unique=True, postgresql_where=text("is_active")),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    embedding_provider: Mapped[str] = mapped_column(String(50), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(200), nullable=False)
    dim: Mapped[int] = mapped_column(Integer, nullable=False)
    sparse_model: Mapped[str] = mapped_column(String(200), nullable=False)
    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_overlap: Mapped[int] = mapped_column(Integer, nullable=False)
    qdrant_collection: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class DocumentChunk(CreatedAtMixin, Base):
    """A chunk of a document's text. ``id`` is also the Qdrant point id (Qdrant is rebuildable from here)."""

    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "index_version", "chunk_index", name="uq_document_chunks_position"),
        Index("ix_document_chunks_org", "organization_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    index_version: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # text | table | rows
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    sheet_name: Mapped[str | None] = mapped_column(String(255))
    row_start: Mapped[int | None] = mapped_column(Integer)
    row_end: Mapped[int | None] = mapped_column(Integer)
    section_path: Mapped[str | None] = mapped_column(String(1000))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    flags: Mapped[list[str]] = mapped_column(ARRAY(String(32)), nullable=False, default=list)
