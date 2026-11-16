"""Request/response models of the internal (n8n -> FastAPI) API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import DocumentStatus

JobState = Literal["queued", "running", "succeeded", "failed"]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: uuid.UUID = Field(
        description="The organization the event belongs to; ids of other organizations 404"
    )
    execution_id: str | None = Field(default=None, max_length=100, description="n8n execution id, for traceability")


class ProcessRequest(_Body):
    job_id: uuid.UUID
    event_id: str | None = Field(default=None, max_length=200, description="Id of the webhook event (logging only)")


class RetryRequest(_Body):
    pass


class WorkflowStatusUpdate(_Body):
    state: Literal["succeeded", "failed", "exhausted"] = Field(
        description=(
            "succeeded: the workflow saw the job succeed · failed: permanent failure · exhausted: retries used up"
        )
    )
    message: str | None = Field(default=None, max_length=500, description="Short reason; never document content")


class JobStatusRead(BaseModel):
    job_id: uuid.UUID
    document_id: uuid.UUID
    organization_id: uuid.UUID
    state: JobState
    document_status: DocumentStatus
    stage: str | None
    attempt: int
    retry_count: int
    max_retries: int
    retryable: bool = Field(description="The last failure was temporary")
    can_retry: bool = Field(description="Failed, temporary, and retries are left")
    retry_after_seconds: int | None = Field(description="Backoff before the next retry (exponential, capped)")
    error_code: str | None
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    workflow_status: str | None


class ProcessResult(JobStatusRead):
    started: bool = Field(description="False when this call changed nothing (duplicate delivery)")


class RetryResult(JobStatusRead):
    retried: bool = Field(description="False when this call changed nothing (already queued, running or done)")
