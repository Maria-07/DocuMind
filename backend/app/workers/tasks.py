"""Background tasks executed by the arq worker. Payloads are ids only."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from arq import Retry
from sqlalchemy import select

from app.models import Document, DocumentPermission, EmbeddingConfig, IngestionJob
from app.models.enums import JobStatus
from app.rag.ingestion.pipeline import IngestionPipeline, RetryLater
from app.rag.vectorstore import QdrantVectorStore
from app.services.access_policy import document_acl_tokens
from app.services.job_queue import INGEST_DOCUMENT

log = structlog.get_logger(__name__)

# A queued job older than this without having started is assumed lost (e.g. Redis was down).
STALE_QUEUED_AFTER = timedelta(minutes=10)


async def ingest_document(ctx: dict[str, Any], document_id: str, job_id: str) -> str:
    pipeline: IngestionPipeline = ctx["pipeline"]
    structlog.contextvars.bind_contextvars(document_id=document_id, job_id=job_id)
    try:
        return await pipeline.run(uuid.UUID(document_id), uuid.UUID(job_id), attempt=ctx.get("job_try", 1))
    except RetryLater as exc:
        raise Retry(defer=exc.defer_seconds) from exc
    finally:
        structlog.contextvars.unbind_contextvars("document_id", "job_id")


async def sync_document_payload(ctx: dict[str, Any], document_id: str) -> str:
    """Push access-control and display fields from PostgreSQL to the document's Qdrant points."""
    store: QdrantVectorStore = ctx["store"]
    doc_uuid = uuid.UUID(document_id)
    async with ctx["sessionmaker"]() as s:
        document = await s.get(Document, doc_uuid)
        if document is None or document.deleted_at is not None or document.embedding_config_id is None:
            return "skipped"
        config = await s.get(EmbeddingConfig, document.embedding_config_id)
        grants = (
            (await s.execute(select(DocumentPermission).where(DocumentPermission.document_id == doc_uuid)))
            .scalars()
            .all()
        )
        payload = {
            "acl": document_acl_tokens(document, grants),
            "category_id": str(document.category_id) if document.category_id else None,
            "document_title": document.title,
        }
    if config is None:
        return "skipped"
    await store.set_document_payload(config.qdrant_collection, doc_uuid, payload)
    return "synced"


async def purge_document(ctx: dict[str, Any], document_id: str) -> str:
    """Remove the stored file, chunks' vectors (all collections) of a soft-deleted document. Idempotent."""
    store: QdrantVectorStore = ctx["store"]
    doc_uuid = uuid.UUID(document_id)
    async with ctx["sessionmaker"]() as session:
        document = await session.get(Document, doc_uuid)
        if document is None:
            return "missing"
        if document.deleted_at is None:
            # Never purge a live document, whatever the queue says.
            log.warning("purge_skipped_not_deleted", document_id=document_id)
            return "not_deleted"
        if document.purged_at is not None:
            return "already_purged"

        await store.delete_document_everywhere(doc_uuid)
        await ctx["storage"].delete(document.storage_key)
        document.purged_at = datetime.now(UTC)
        await session.commit()

    log.info("document_purged", document_id=document_id)
    return "purged"


async def sweep_jobs(ctx: dict[str, Any]) -> dict[str, int]:
    """Recover ingestion jobs lost by the queue or by a crashed worker (runs on a cron)."""
    settings = ctx["settings"]
    now = datetime.now(UTC)
    running_timeout = timedelta(seconds=settings.worker_job_timeout_seconds + 300)
    requeued = recovered = 0
    async with ctx["sessionmaker"]() as s:
        stale_queued = (
            (
                await s.execute(
                    select(IngestionJob).where(
                        IngestionJob.status == JobStatus.QUEUED, IngestionJob.created_at < now - STALE_QUEUED_AFTER
                    )
                )
            )
            .scalars()
            .all()
        )
        stuck_running = (
            (
                await s.execute(
                    select(IngestionJob).where(
                        IngestionJob.status == JobStatus.RUNNING, IngestionJob.started_at < now - running_timeout
                    )
                )
            )
            .scalars()
            .all()
        )
        for job in stuck_running:
            job.status, job.error = JobStatus.QUEUED, "Worker stopped while processing; re-queued"
            recovered += 1
        await s.commit()
        to_enqueue = [*stale_queued, *stuck_running]

    bucket = int(now.timestamp() // 600)
    for job in to_enqueue:
        # A new arq job id per sweep window; the pipeline's row locks make duplicates harmless.
        enqueued = await ctx["redis"].enqueue_job(
            INGEST_DOCUMENT, str(job.document_id), str(job.id), _job_id=f"ingest:{job.id}:sweep{bucket}"
        )
        requeued += enqueued is not None
    if requeued or recovered:
        log.info("jobs_swept", requeued=requeued, recovered_running=recovered)
    return {"requeued": requeued, "recovered_running": recovered}
