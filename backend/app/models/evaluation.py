from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ARRAY, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin


class DocumentComparison(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "document_comparisons"
    __table_args__ = (Index("ix_document_comparisons_user_created", "user_id", "created_at"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    document_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid), nullable=False)
    question: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # complete | error
    result: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    model: Mapped[str | None] = mapped_column(String(100))
    usage: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)


class EvalDataset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "eval_datasets"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_eval_datasets_org_name"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))


class EvalCase(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A labelled question. Ground truth survives re-chunking: ``evidence`` quotes are resolved to the
    chunks of whatever index version / embedding config is being evaluated."""

    __tablename__ = "eval_cases"
    __table_args__ = (Index("ix_eval_cases_dataset", "dataset_id"),)

    dataset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("eval_datasets.id", ondelete="CASCADE"), nullable=False
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    expected_answer: Mapped[str | None] = mapped_column(Text)
    # Empty = the documents do not answer this question; the correct behaviour is to say so.
    relevant_document_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid), nullable=False, default=list)
    # Optional pinned chunk ids (valid only for the index version they came from).
    relevant_chunk_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid), nullable=False, default=list)
    # [{"document_id": "...", "quote": "exact text from the document"}]
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(50)), nullable=False, default=list)


class EvalRun(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "eval_runs"
    __table_args__ = (Index("ix_eval_runs_dataset_created", "dataset_id", "created_at"),)

    dataset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("eval_datasets.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # queued | running | complete | failed
    # Everything that can change results: embedding/chunking config, retrieval params, reranker, LLM, prompt.
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    baseline_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("eval_runs.id", ondelete="SET NULL"))
    case_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_cases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvalCaseResult(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "eval_case_results"
    __table_args__ = (UniqueConstraint("run_id", "case_id", name="uq_eval_case_results_run_case"),)

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=False)
    case_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("eval_cases.id", ondelete="SET NULL"))
    question: Mapped[str] = mapped_column(Text, nullable=False)  # snapshot
    retrieved_chunk_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid), nullable=False, default=list)
    relevant_chunk_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid), nullable=False, default=list)
    answer: Mapped[str | None] = mapped_column(Text)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    judge: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    cost_tokens: Mapped[int | None] = mapped_column(Integer)
    top_relevance: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)
