"""n8n workflow integration: the webhook that announces uploads, and the operations n8n calls back.

Division of labour
------------------
FastAPI keeps everything about documents (validation, parsing, chunking, embeddings, Qdrant, access
control, status). n8n only orchestrates: it is told about an upload, asks FastAPI to start processing,
polls the job, waits and retries temporary failures with backoff, and reports the outcome / notifies.

* PostgreSQL is the single source of truth for status; n8n holds no state that is not recoverable from
  ``GET /internal/jobs/{id}/status``.
* Every operation is idempotent, so a duplicated webhook, a repeated poll or a re-delivered retry is
  harmless, and each is scoped to the organization named in the call.
* Webhook payloads carry ids and metadata only: never file contents, extracted text or links.
"""

from __future__ import annotations

import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import AuthenticationError, ConflictError, NotFoundError
from app.models import Document, IngestionJob
from app.models.enums import DocumentStatus, JobStatus
from app.schemas.workflow import JobState, JobStatusRead, ProcessResult, RetryResult
from app.services.job_queue import INGEST_DOCUMENT, JobQueue

log = structlog.get_logger(__name__)

WEBHOOK_SECRET_HEADER = "X-DocuMind-Webhook-Secret"  # noqa: S105 - header name, not a secret
INTERNAL_KEY_HEADER = "X-Internal-Api-Key"
UPLOAD_EVENT = "document.uploaded"

ReportedState = Literal["succeeded", "failed", "exhausted"]


def _now() -> datetime:
    return datetime.now(UTC)


def backoff_seconds(retry_count: int, settings: Settings) -> int:
    """Delay before retry number ``retry_count + 1``: base * 2^retry_count, capped (exponential backoff)."""
    return int(min(settings.n8n_retry_base_seconds * (2 ** max(retry_count, 0)), settings.n8n_retry_max_seconds))


def check_internal_key(settings: Settings, presented: str | None) -> None:
    """Authenticate an n8n -> FastAPI call (constant-time comparison)."""
    expected = settings.internal_api_key.get_secret_value() if settings.internal_api_key else ""
    if not expected or not presented or not hmac.compare_digest(presented.encode(), expected.encode()):
        raise AuthenticationError("Invalid or missing internal API key")


# --- the upload webhook (FastAPI -> n8n) ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UploadEvent:
    """What n8n is told about an upload. Metadata only."""

    event_id: str
    document_id: uuid.UUID
    job_id: uuid.UUID
    organization_id: uuid.UUID
    file_type: str
    size_bytes: int
    title: str | None  # only when N8N_INCLUDE_TITLE_IN_EVENTS is on
    uploaded_by: uuid.UUID | None
    occurred_at: datetime

    def payload(self) -> dict[str, Any]:
        return {
            "event": UPLOAD_EVENT,
            "event_id": self.event_id,
            "occurred_at": self.occurred_at.isoformat(),
            "document_id": str(self.document_id),
            "job_id": str(self.job_id),
            "organization_id": str(self.organization_id),
            "file_type": self.file_type,
            "size_bytes": self.size_bytes,
            "title": self.title,
            "uploaded_by": str(self.uploaded_by) if self.uploaded_by else None,
        }


class WorkflowNotifier(Protocol):
    async def document_uploaded(self, event: UploadEvent) -> bool:
        """Deliver the event; True only if n8n accepted it (2xx)."""
        ...


class N8nWebhookNotifier:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._url = settings.n8n_webhook_url
        self._secret = settings.n8n_webhook_secret.get_secret_value() if settings.n8n_webhook_secret else ""
        self._timeout = settings.n8n_webhook_timeout_seconds
        self._client = client

    async def document_uploaded(self, event: UploadEvent) -> bool:
        headers = {WEBHOOK_SECRET_HEADER: self._secret, "Content-Type": "application/json"}
        try:
            if self._client is not None:
                response = await self._client.post(self._url, json=event.payload(), headers=headers)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(self._url, json=event.payload(), headers=headers)
        except httpx.HTTPError as exc:
            log.warning("n8n_webhook_failed", error=type(exc).__name__, job_id=str(event.job_id))
            return False
        if response.is_success:
            return True
        # 404 here usually means the workflow is not active (production webhooks only exist while active).
        log.warning("n8n_webhook_rejected", status=response.status_code, job_id=str(event.job_id))
        return False


# --- operations n8n calls back (n8n -> FastAPI) ------------------------------------------------------


def _state(job: IngestionJob) -> JobState:
    if job.status == JobStatus.QUEUED:
        return "queued"
    if job.status == JobStatus.RUNNING:
        return "running"
    return "succeeded" if job.status == JobStatus.SUCCEEDED else "failed"


class WorkflowService:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], settings: Settings, queue: JobQueue) -> None:
        self.sessionmaker = sessionmaker
        self.settings = settings
        self.queue = queue

    # --- lookups (organization-scoped: another organization's ids are simply "not found") ----------
    async def _job(
        self, s: AsyncSession, organization_id: uuid.UUID, job_id: uuid.UUID
    ) -> tuple[IngestionJob, Document]:
        row = (
            await s.execute(
                select(IngestionJob, Document)
                .join(Document, Document.id == IngestionJob.document_id)
                .where(IngestionJob.id == job_id, Document.organization_id == organization_id)
                .with_for_update(of=IngestionJob)
            )
        ).first()
        if row is None:
            raise NotFoundError("Job not found", code="job_not_found")
        return row[0], row[1]

    def _status(self, job: IngestionJob, document: Document) -> JobStatusRead:
        failed = job.status == JobStatus.FAILED
        can_retry = failed and job.retryable and job.retry_count < self.settings.n8n_max_retries
        return JobStatusRead(
            job_id=job.id,
            document_id=job.document_id,
            organization_id=document.organization_id,
            state=_state(job),
            document_status=document.status,
            stage=job.stage,
            attempt=job.attempt,
            retry_count=job.retry_count,
            max_retries=self.settings.n8n_max_retries,
            retryable=job.retryable,
            can_retry=can_retry,
            retry_after_seconds=backoff_seconds(job.retry_count, self.settings) if can_retry else None,
            error_code=job.error_code,
            error=job.error,
            started_at=job.started_at,
            finished_at=job.finished_at,
            workflow_status=job.workflow_status,
        )

    async def status(self, organization_id: uuid.UUID, job_id: uuid.UUID) -> JobStatusRead:
        async with self.sessionmaker() as s:
            job, document = await self._job(s, organization_id, job_id)
            return self._status(job, document)

    # --- start processing -----------------------------------------------------------------------------
    async def process(
        self, organization_id: uuid.UUID, document_id: uuid.UUID, job_id: uuid.UUID, execution_id: str | None
    ) -> ProcessResult:
        """Queue the existing ingestion pipeline for this job (idempotent: at most one queued run per attempt)."""
        enqueue = False
        async with self.sessionmaker() as s:
            job, document = await self._job(s, organization_id, job_id)
            if job.document_id != document_id:
                raise NotFoundError("Job not found", code="job_not_found")
            if document.deleted_at is not None:
                raise ConflictError("The document was deleted", code="document_deleted")
            if not job.workflow_managed:
                raise ConflictError("This job is not managed by the workflow", code="job_not_managed")
            if execution_id:
                job.workflow_execution_id = execution_id[:100]
            started = False
            if job.status == JobStatus.QUEUED and job.workflow_status in (None, "triggered"):
                job.workflow_status, job.workflow_updated_at = "queued", _now()
                if document.status == DocumentStatus.UPLOADED:
                    document.status = DocumentStatus.QUEUED
                started, enqueue = True, True
            await s.commit()
            result = ProcessResult(started=started, **self._status(job, document).model_dump())
        if enqueue:
            await self._enqueue(document_id, job_id, result.retry_count)
        return result

    async def _enqueue(self, document_id: uuid.UUID, job_id: uuid.UUID, retry_count: int) -> None:
        # Deterministic id per (job, retry): the queue itself refuses a second copy of the same attempt.
        await self.queue.enqueue(
            INGEST_DOCUMENT, str(document_id), str(job_id), job_id=f"ingest:{job_id}:r{retry_count}"
        )

    # --- retry --------------------------------------------------------------------------------------
    async def retry(self, organization_id: uuid.UUID, job_id: uuid.UUID, execution_id: str | None) -> RetryResult:
        """Re-queue a job that failed with a temporary error (idempotent; bounded by N8N_MAX_RETRIES)."""
        enqueue = False
        async with self.sessionmaker() as s:
            job, document = await self._job(s, organization_id, job_id)
            document_id = document.id
            if document.deleted_at is not None:
                raise ConflictError("The document was deleted", code="document_deleted")
            if job.status == JobStatus.FAILED:
                if not job.retryable:
                    raise ConflictError("This failure is permanent and cannot be retried", code="not_retryable")
                if job.retry_count >= self.settings.n8n_max_retries:
                    raise ConflictError("The maximum number of retries was reached", code="retries_exhausted")
                job.status, job.finished_at = JobStatus.QUEUED, None
                job.retry_count += 1
                job.retryable = False
                job.workflow_status, job.workflow_updated_at = "queued", _now()
                if execution_id:
                    job.workflow_execution_id = execution_id[:100]
                enqueue = True
            await s.commit()
            result = RetryResult(retried=enqueue, **self._status(job, document).model_dump())
        if enqueue:
            await self._enqueue(document_id, job_id, result.retry_count)
        return result

    # --- outcome reported by the workflow ------------------------------------------------------------------
    async def record(
        self,
        organization_id: uuid.UUID,
        job_id: uuid.UUID,
        *,
        state: ReportedState,
        execution_id: str | None,
        message: str | None,
    ) -> JobStatusRead:
        """The workflow's verdict. PostgreSQL stays authoritative: success can only be *recorded* once the
        pipeline has actually finished; a failure is finalized here (permanent, or retries exhausted)."""
        async with self.sessionmaker() as s:
            job, document = await self._job(s, organization_id, job_id)
            if execution_id:
                job.workflow_execution_id = execution_id[:100]
            if state == "succeeded":
                if job.status != JobStatus.SUCCEEDED:
                    raise ConflictError("The job has not succeeded", code="job_not_succeeded")
                job.workflow_status, job.workflow_updated_at = "succeeded", _now()
            elif job.status == JobStatus.SUCCEEDED:
                raise ConflictError("The job already succeeded", code="job_already_succeeded")
            elif job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
                raise ConflictError("The job is still in progress", code="job_in_progress")
            else:
                self._finalize_failure(job, document, message, exhausted=state == "exhausted")
            await s.commit()
            return self._status(job, document)

    def _finalize_failure(self, job: IngestionJob, document: Document, message: str | None, *, exhausted: bool) -> None:
        job.status, job.retryable = JobStatus.FAILED, False
        job.workflow_status, job.workflow_updated_at = "failed", _now()
        if job.finished_at is None:
            job.finished_at = _now()
        if exhausted:
            reason = f"Processing failed after {job.retry_count} retries ({job.error_code or 'error'})"
        else:
            reason = message or document.error_message or "Processing failed"
        reason = reason[:500]
        if document.deleted_at is None:
            if document.active_index_version > 0:
                document.error_message = f"Reprocessing failed: {reason}"  # the previous version stays searchable
            else:
                document.status, document.error_message = DocumentStatus.FAILED, reason

    # --- safety net (called from the sweep) -------------------------------------------------------------
    async def finalize_stalled(self) -> int:
        """Fail jobs that have been waiting for a retry longer than N8N_STALL_MINUTES (the workflow is gone)."""
        cutoff = _now() - timedelta(minutes=self.settings.n8n_stall_minutes)
        finalized = 0
        async with self.sessionmaker() as s:
            rows = (
                await s.execute(
                    select(IngestionJob, Document)
                    .join(Document, Document.id == IngestionJob.document_id)
                    .where(
                        IngestionJob.workflow_managed.is_(True),
                        IngestionJob.status == JobStatus.FAILED,
                        IngestionJob.retryable.is_(True),
                        IngestionJob.workflow_updated_at < cutoff,
                    )
                    .with_for_update(of=IngestionJob)
                )
            ).all()
            for job, document in rows:
                self._finalize_failure(job, document, "The retry workflow stopped responding", exhausted=False)
                finalized += 1
            await s.commit()
        if finalized:
            log.warning("workflow_jobs_stalled", count=finalized)
        return finalized
