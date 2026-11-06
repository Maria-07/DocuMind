"""Background job queue abstraction (arq on Redis). Job payloads are ids only — never document content."""

from __future__ import annotations

from typing import Protocol

from arq.connections import ArqRedis


class JobQueue(Protocol):
    async def enqueue(self, function: str, *args: str, job_id: str | None = None) -> bool:
        """Return True if enqueued, False if a job with ``job_id`` is already queued."""
        ...


class ArqJobQueue:
    def __init__(self, pool: ArqRedis) -> None:
        self._pool = pool

    async def enqueue(self, function: str, *args: str, job_id: str | None = None) -> bool:
        job = await self._pool.enqueue_job(function, *args, _job_id=job_id)
        return job is not None


# Job function names (implemented in app.workers.tasks).
INGEST_DOCUMENT = "ingest_document"
PURGE_DOCUMENT = "purge_document"
