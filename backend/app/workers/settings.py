"""arq worker entrypoint: ``arq app.workers.settings.WorkerSettings``."""

from __future__ import annotations

from typing import Any

import structlog
from arq import cron, func
from arq.connections import RedisSettings
from qdrant_client import AsyncQdrantClient

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import create_engine, create_sessionmaker
from app.rag.evaluation.runner import EvaluationRunner
from app.rag.generation import build_judge
from app.rag.runtime import build_rag_runtime
from app.rag.setup import build_ingestion_pipeline
from app.services.storage import LocalStorage
from app.workers.tasks import ingest_document, purge_document, run_evaluation, sweep_jobs, sync_document_payload

_settings = get_settings()
log = structlog.get_logger(__name__)


async def startup(ctx: dict[str, Any]) -> None:
    configure_logging(_settings.log_level, _settings.log_json)
    engine = create_engine(_settings)
    sessionmaker = create_sessionmaker(engine)
    storage = LocalStorage(_settings.storage_local_path)
    qdrant = AsyncQdrantClient(
        url=_settings.qdrant_url,
        api_key=_settings.qdrant_api_key.get_secret_value() if _settings.qdrant_api_key else None,
        timeout=60,
    )
    # Loads the embedding models (downloaded on first run) and ensures the Qdrant collection exists.
    pipeline = await build_ingestion_pipeline(
        settings=_settings, sessionmaker=sessionmaker, storage=storage, qdrant=qdrant
    )
    # Evaluations reuse the loaded embedding models; the reranker/LLM load lazily on first use.
    rag = build_rag_runtime(_settings, sessionmaker, qdrant, embedder=pipeline.embedder, sparse=pipeline.sparse)
    ctx["evaluation_runner"] = EvaluationRunner(
        sessionmaker=sessionmaker,
        settings=_settings,
        retriever=rag.retriever,
        llm=rag.llm,
        judge=build_judge(_settings) or rag.llm,
    )
    ctx.update(
        settings=_settings,
        engine=engine,
        sessionmaker=sessionmaker,
        storage=storage,
        qdrant=qdrant,
        pipeline=pipeline,
        store=pipeline.store,
    )
    log.info("worker_ready", collection=pipeline.collection, embedding_model=_settings.embedding_model)
    await sweep_jobs(ctx)  # pick up anything queued while no worker was running


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["qdrant"].close()
    await ctx["engine"].dispose()


class WorkerSettings:
    # Evaluation runs call the LLM per case and can take much longer than an ingestion job.
    functions = [
        ingest_document,
        sync_document_payload,
        purge_document,
        func(run_evaluation, timeout=3600, max_tries=1),
    ]
    cron_jobs = [cron(sweep_jobs, minute=set(range(0, 60, 5)), run_at_startup=False)]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    # Embedding is CPU-heavy: a few concurrent jobs per worker; scale with more worker processes.
    max_jobs = _settings.worker_max_jobs
    max_tries = _settings.ingestion_max_tries
    job_timeout = _settings.worker_job_timeout_seconds
    keep_result = 3600
    # Heartbeat for `arq --check` (container health checks): written every minute while alive.
    health_check_interval = 60
