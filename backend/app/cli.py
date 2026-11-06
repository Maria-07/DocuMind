"""Operational commands.

    python -m app.cli reindex --stale     # documents not indexed with the active embedding config
    python -m app.cli reindex --all       # every live document (e.g. after a parser improvement)
    python -m app.cli reindex --failed    # retry documents whose processing failed

Re-indexing never makes a document unsearchable: the previous version serves until the new one is
complete (see app.rag.ingestion.pipeline).
"""

from __future__ import annotations

import argparse
import asyncio

from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import exists, or_, select

from app.core.config import get_settings
from app.db.session import create_engine, create_sessionmaker
from app.models import Document, EmbeddingConfig, IngestionJob
from app.models.enums import DocumentStatus, JobStatus
from app.rag.vectorstore import collection_name
from app.services.job_queue import INGEST_DOCUMENT
from app.utils.ids import uuid7


async def reindex(mode: str) -> None:
    settings = get_settings()
    engine = create_engine(settings, null_pool=True)
    sessionmaker = create_sessionmaker(engine)
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    active_name = collection_name(
        settings.embedding_model, settings.sparse_model, settings.chunk_size_tokens, settings.chunk_overlap_tokens
    )
    try:
        async with sessionmaker() as s:
            active = (
                await s.execute(select(EmbeddingConfig).where(EmbeddingConfig.name == active_name))
            ).scalar_one_or_none()
            in_flight = exists().where(
                IngestionJob.document_id == Document.id,
                IngestionJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
            )
            stmt = select(Document).where(Document.deleted_at.is_(None), ~in_flight)
            if mode == "stale":
                stale = Document.embedding_config_id.is_(None)
                if active is not None:
                    stale = or_(stale, Document.embedding_config_id != active.id)
                stmt = stmt.where(stale)
            elif mode == "failed":
                stmt = stmt.where(Document.status == DocumentStatus.FAILED)
            documents = (await s.execute(stmt)).scalars().all()

            jobs = [
                IngestionJob(id=uuid7(), document_id=d.id, status=JobStatus.QUEUED, attempt=0, metrics={})
                for d in documents
            ]
            s.add_all(jobs)
            await s.commit()

        for job in jobs:
            await pool.enqueue_job(INGEST_DOCUMENT, str(job.document_id), str(job.id), _job_id=f"ingest:{job.id}")
        print(f"Queued {len(jobs)} document(s) for re-indexing (mode={mode}, active config={active_name}).")
        if active is None:
            print("Note: the active config is registered when the worker starts.")
    finally:
        await pool.aclose()
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    reindex_cmd = sub.add_parser("reindex", help="Queue documents for (re-)indexing")
    group = reindex_cmd.add_mutually_exclusive_group(required=True)
    group.add_argument("--stale", action="store_const", const="stale", dest="mode")
    group.add_argument("--all", action="store_const", const="all", dest="mode")
    group.add_argument("--failed", action="store_const", const="failed", dest="mode")
    args = parser.parse_args()
    if args.command == "reindex":
        asyncio.run(reindex(args.mode))


if __name__ == "__main__":
    main()
