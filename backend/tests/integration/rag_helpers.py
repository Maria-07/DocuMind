"""Helpers shared by ingestion / retrieval / chat integration tests."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core.resources import AppResources
from app.models import IngestionJob
from app.rag.ingestion.pipeline import IngestionPipeline


async def latest_job(resources: AppResources, document_id: str) -> IngestionJob:
    async with resources.sessionmaker() as s:
        return (
            await s.execute(
                select(IngestionJob)
                .where(IngestionJob.document_id == uuid.UUID(document_id))
                .order_by(IngestionJob.created_at.desc(), IngestionJob.id.desc())
                .limit(1)
            )
        ).scalar_one()


async def run_ingestion(
    pipeline: IngestionPipeline, resources: AppResources, document_id: str, attempt: int = 1
) -> str:
    job = await latest_job(resources, document_id)
    return await pipeline.run(uuid.UUID(document_id), job.id, attempt=attempt)
