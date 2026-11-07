from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import ORMModel


class EvidenceQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: uuid.UUID
    quote: str = Field(min_length=3, max_length=2000, description="Exact text from the document")


class EvalCaseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=3, max_length=2000)
    expected_answer: str | None = Field(default=None, max_length=5000)
    relevant_document_ids: list[uuid.UUID] = Field(
        default_factory=list, max_length=20, description="Empty: the documents don't answer it (expect abstention)"
    )
    evidence: list[EvidenceQuote] = Field(default_factory=list, max_length=20)
    tags: list[str] = Field(default_factory=list, max_length=10)


class EvalCaseRead(ORMModel):
    id: uuid.UUID
    question: str
    expected_answer: str | None
    relevant_document_ids: list[uuid.UUID]
    evidence: list[dict[str, Any]]
    tags: list[str]
    created_at: datetime


class EvalDatasetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class EvalDatasetRead(ORMModel):
    id: uuid.UUID
    name: str
    description: str | None
    created_at: datetime
    case_count: int = 0
    last_run: EvalRunSummary | None = None


class EvalRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: uuid.UUID
    baseline_run_id: uuid.UUID | None = None


class EvalRunSummary(ORMModel):
    id: uuid.UUID
    dataset_id: uuid.UUID
    status: str
    config: dict[str, Any]
    metrics: dict[str, Any]
    baseline_run_id: uuid.UUID | None
    case_count: int
    completed_cases: int
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class EvalCaseResultRead(ORMModel):
    id: uuid.UUID
    case_id: uuid.UUID | None
    question: str
    answer: str | None
    citations: list[dict[str, Any]]
    metrics: dict[str, Any]
    judge: dict[str, Any]
    latency_ms: int | None
    top_relevance: float | None
    error: str | None


class EvalRunDetail(EvalRunSummary):
    results: list[EvalCaseResultRead]
    baseline_metrics: dict[str, Any] | None = None


class MetricPoint(BaseModel):
    run_id: uuid.UUID
    finished_at: datetime | None
    metrics: dict[str, Any]
    config: dict[str, Any]


EvalDatasetRead.model_rebuild()
