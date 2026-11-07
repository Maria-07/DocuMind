"""Embedding configurations, index statistics and re-indexing (shared by the API and the CLI)."""

from __future__ import annotations

import uuid
from typing import Any, Literal

import structlog
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qm
from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Document, EmbeddingConfig, IngestionJob
from app.models.enums import DocumentStatus, JobStatus
from app.services.job_queue import INGEST_DOCUMENT, JobQueue
from app.utils.ids import uuid7

log = structlog.get_logger(__name__)

ReindexMode = Literal["stale", "failed", "all"]


async def queue_reindex(
    sessionmaker: async_sessionmaker[AsyncSession],
    queue: JobQueue,
    mode: ReindexMode,
    organization_id: uuid.UUID | None = None,
    requested_by: uuid.UUID | None = None,
) -> int:
    """Create ingestion jobs for matching documents and enqueue them. Returns the number queued.

    stale: not indexed with the active embedding config · failed: processing failed · all: everything.
    Documents that already have a queued/running job are skipped.
    """
    async with sessionmaker() as s:
        active = await s.scalar(select(EmbeddingConfig.id).where(EmbeddingConfig.is_active.is_(True)))
        in_flight = exists().where(
            IngestionJob.document_id == Document.id, IngestionJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])
        )
        stmt = select(Document).where(Document.deleted_at.is_(None), ~in_flight)
        if organization_id is not None:
            stmt = stmt.where(Document.organization_id == organization_id)
        if mode == "stale":
            stale = Document.embedding_config_id.is_(None)
            if active is not None:
                stale = or_(stale, Document.embedding_config_id != active)
            stmt = stmt.where(stale, Document.status != DocumentStatus.FAILED)
        elif mode == "failed":
            stmt = stmt.where(Document.status == DocumentStatus.FAILED)
        documents = (await s.execute(stmt)).scalars().all()
        jobs = [
            IngestionJob(
                id=uuid7(), document_id=d.id, status=JobStatus.QUEUED, attempt=0, metrics={}, requested_by=requested_by
            )
            for d in documents
        ]
        s.add_all(jobs)
        await s.commit()
    for job in jobs:
        await queue.enqueue(INGEST_DOCUMENT, str(job.document_id), str(job.id), job_id=f"ingest:{job.id}")
    log.info("reindex_queued", mode=mode, count=len(jobs))
    return len(jobs)


async def embedding_overview(
    sessionmaker: async_sessionmaker[AsyncSession], qdrant: AsyncQdrantClient | None, organization_id: uuid.UUID
) -> dict[str, Any]:
    async with sessionmaker() as s:
        configs = (await s.execute(select(EmbeddingConfig).order_by(EmbeddingConfig.created_at.desc()))).scalars().all()
        live = (Document.organization_id == organization_id, Document.deleted_at.is_(None))
        per_config = dict(
            (
                await s.execute(
                    select(Document.embedding_config_id, func.count())
                    .where(*live)
                    .group_by(Document.embedding_config_id)
                )
            ).all()
        )
        total = await s.scalar(select(func.count()).where(*live))
        failed = await s.scalar(select(func.count()).where(*live, Document.status == DocumentStatus.FAILED))
        chunks = dict(
            (
                await s.execute(
                    select(Document.embedding_config_id, func.coalesce(func.sum(Document.chunk_count), 0))
                    .where(*live)
                    .group_by(Document.embedding_config_id)
                )
            ).all()
        )
    active = next((c for c in configs if c.is_active), None)
    out = []
    for c in configs:
        points: int | None = None
        if qdrant is not None:
            try:
                if await qdrant.collection_exists(c.qdrant_collection):
                    points = (
                        await qdrant.count(
                            c.qdrant_collection,
                            count_filter=qm.Filter(
                                must=[qm.FieldCondition(key="org_id", match=qm.MatchValue(value=str(organization_id)))]
                            ),
                            exact=True,
                        )
                    ).count
            except Exception:
                log.warning("qdrant_count_failed", collection=c.qdrant_collection)
        out.append(
            {
                "id": str(c.id),
                "name": c.name,
                "is_active": c.is_active,
                "embedding_provider": c.embedding_provider,
                "embedding_model": c.embedding_model,
                "dim": c.dim,
                "sparse_model": c.sparse_model,
                "chunk_size": c.chunk_size,
                "chunk_overlap": c.chunk_overlap,
                "collection": c.qdrant_collection,
                "documents": int(per_config.get(c.id, 0)),
                "chunks": int(chunks.get(c.id, 0)),
                "points": points,
                "created_at": c.created_at.isoformat(),
            }
        )
    active_id = active.id if active else None
    return {
        "configs": out,
        "documents_total": int(total or 0),
        "documents_on_active": int(per_config.get(active_id, 0)) if active_id else 0,
        # Indexed with an older embedding/chunking config: still searchable only via that collection.
        "documents_stale": sum(int(n) for cid, n in per_config.items() if cid is not None and cid != active_id),
        # Never indexed yet (queued, processing, or failed on the first attempt).
        "documents_not_indexed": int(per_config.get(None, 0)),
        "documents_failed": int(failed or 0),
    }
