"""The document ingestion pipeline (runs in the arq worker).

    claim job → parse → chunk → embed (dense + BM25) → store chunks (PG) → upsert points (Qdrant,
    searchable=false) → finalize: flip to searchable + current ACL, drop older versions, mark INDEXED

Guarantees
----------
* **Never partially searchable.** Points are written with ``searchable=false`` and flipped only
  after every point of the new ``index_version`` is stored.
* **Reprocessing never causes downtime.** The previous version stays searchable until the new one
  is complete; a failed reprocess keeps the previous version.
* **Idempotent retries.** Point ids are deterministic (doc, version, chunk index) and every attempt
  first clears what an earlier attempt of the same version may have left.
* **Deletes win.** If the document is deleted while processing, the new version is discarded.
* **Fresh ACLs.** Access tokens are recomputed from PostgreSQL at finalize time.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import anyio
import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models import Document, DocumentChunk, DocumentPermission, EmbeddingConfig, IngestionJob
from app.models.enums import DocumentStatus, JobStatus
from app.rag.chunking.chunker import Chunk, Chunker
from app.rag.embeddings.base import EmbeddingProvider, SparseEncoder
from app.rag.ingestion.parsers import parse_document
from app.rag.ingestion.safety import content_flags
from app.rag.ingestion.types import IngestionError, ParsedDocument, PermanentIngestionError
from app.rag.vectorstore import ChunkPoint, QdrantVectorStore
from app.services.access_policy import document_acl_tokens
from app.services.storage import LocalStorage

log = structlog.get_logger(__name__)

_POINT_NAMESPACE = uuid.UUID("6b3f5b8e-2f4a-4c55-9a7e-3c1f0d9e8a21")


def point_id(document_id: uuid.UUID, index_version: int, chunk_index: int) -> uuid.UUID:
    return uuid.uuid5(_POINT_NAMESPACE, f"{document_id}:{index_version}:{chunk_index}")


def embedding_text(title: str, chunk: Chunk) -> str:
    """Text that is embedded: a short context header + the chunk (stored/displayed text excludes it)."""
    header = [f"Document: {title}"]
    if chunk.section_path:
        header.append(f"Section: {chunk.section_path}")
    if chunk.sheet_name:
        header.append(f"Sheet: {chunk.sheet_name}")
    return "\n".join(header) + "\n\n" + chunk.text


class RetryLater(Exception):
    def __init__(self, defer_seconds: int) -> None:
        super().__init__(f"retry in {defer_seconds}s")
        self.defer_seconds = defer_seconds


@dataclass(slots=True)
class _Claim:
    organization_id: uuid.UUID
    file_type: Any
    storage_key: str
    title: str
    category_id: uuid.UUID | None
    index_version: int
    previous_version: int
    previous_config_id: uuid.UUID | None


def _now() -> datetime:
    return datetime.now(UTC)


class IngestionPipeline:
    def __init__(
        self,
        *,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        storage: LocalStorage,
        store: QdrantVectorStore,
        embedder: EmbeddingProvider,
        sparse: SparseEncoder,
        chunker: Chunker,
        embedding_config: EmbeddingConfig,
    ) -> None:
        self.settings = settings
        self.sessionmaker = sessionmaker
        self.storage = storage
        self.store = store
        self.embedder = embedder
        self.sparse = sparse
        self.chunker = chunker
        self.config = embedding_config
        self.collection = embedding_config.qdrant_collection

    # =======================================================================================
    async def run(self, document_id: uuid.UUID, job_id: uuid.UUID, attempt: int) -> str:
        claim = await self._claim(document_id, job_id, attempt)
        if isinstance(claim, str):
            return claim

        timings: dict[str, float] = {}
        version = claim.index_version
        try:
            started = time.perf_counter()
            parsed: ParsedDocument = await anyio.to_thread.run_sync(
                lambda: parse_document(
                    self.storage.local_path(claim.storage_key),
                    claim.file_type,
                    max_pdf_pages=self.settings.max_pdf_pages,
                    max_sheet_rows=self.settings.max_sheet_rows,
                )
            )
            timings["parse_s"] = time.perf_counter() - started

            await self._set_stage(job_id, "chunk")
            started = time.perf_counter()
            chunks = await anyio.to_thread.run_sync(lambda: self.chunker.chunk(parsed))
            timings["chunk_s"] = time.perf_counter() - started
            if not chunks:
                raise PermanentIngestionError(
                    "no_text", "No extractable text was found (is it a scanned document without a text layer?)"
                )

            await self._set_stage(job_id, "embed")
            started = time.perf_counter()
            texts = [embedding_text(claim.title, c) for c in chunks]
            dense = await self.embedder.embed_documents(texts)
            sparse = await self.sparse.encode_documents(texts)
            timings["embed_s"] = time.perf_counter() - started

            await self._set_stage(job_id, "store")
            started = time.perf_counter()
            flags = [content_flags(c.text) for c in chunks]
            await self._store_chunks(document_id, claim, chunks, flags)
            await self.store.delete_document(self.collection, document_id, index_version=version)  # earlier attempt
            await self.store.upsert(
                self.collection,
                [
                    ChunkPoint(
                        id=point_id(document_id, version, c.index),
                        dense=dense[i],
                        sparse=sparse[i],
                        payload=self._payload(document_id, claim, c, flags[i]),
                    )
                    for i, c in enumerate(chunks)
                ],
            )
            timings["store_s"] = time.perf_counter() - started

            await self._set_stage(job_id, "finalize")
            return await self._finalize(document_id, job_id, claim, parsed, chunks, timings)
        except PermanentIngestionError as exc:
            await self._fail(document_id, job_id, claim, exc.code, exc.message)
            return "failed"
        except Exception as exc:
            log.exception("ingestion_attempt_failed", document_id=str(document_id), attempt=attempt)
            code = exc.code if isinstance(exc, IngestionError) else "processing_error"
            if attempt >= self.settings.ingestion_max_tries:
                await self._fail(
                    document_id,
                    job_id,
                    claim,
                    code,
                    f"Processing failed after {attempt} attempts ({type(exc).__name__})",
                )
                return "failed"
            await self._requeue(document_id, job_id, claim, f"Attempt {attempt} failed ({type(exc).__name__})")
            raise RetryLater(defer_seconds=min(30 * 2 ** (attempt - 1), 600)) from exc

    # --- steps -----------------------------------------------------------------------------
    async def _claim(self, document_id: uuid.UUID, job_id: uuid.UUID, attempt: int) -> _Claim | str:
        async with self.sessionmaker() as s:
            job = await s.get(IngestionJob, job_id, with_for_update=True)
            document = await s.get(Document, document_id, with_for_update=True)
            if job is None or document is None or job.document_id != document.id:
                return "missing"
            if job.status in (JobStatus.SUCCEEDED, JobStatus.FAILED):
                return "already_finished"
            if job.status == JobStatus.RUNNING:
                return "already_running"  # another worker holds it (the sweep recovers crashed ones)
            if document.deleted_at is not None:
                job.status, job.error, job.finished_at = JobStatus.FAILED, "Document was deleted", _now()
                await s.commit()
                return "deleted"

            version = job.index_version or document.active_index_version + 1
            job.status = JobStatus.RUNNING
            job.attempt = attempt
            job.index_version = version
            job.stage = "parse"
            job.started_at = _now()
            job.error = None
            document.status = DocumentStatus.PROCESSING
            claim = _Claim(
                organization_id=document.organization_id,
                file_type=document.file_type,
                storage_key=document.storage_key,
                title=document.title,
                category_id=document.category_id,
                index_version=version,
                previous_version=document.active_index_version,
                previous_config_id=document.embedding_config_id,
            )
            await s.commit()
        log.info("ingestion_started", document_id=str(document_id), version=version, attempt=attempt)
        return claim

    async def _set_stage(self, job_id: uuid.UUID, stage: str) -> None:
        async with self.sessionmaker() as s:
            job = await s.get(IngestionJob, job_id)
            if job is not None:
                job.stage = stage
                await s.commit()

    async def _store_chunks(
        self, document_id: uuid.UUID, claim: _Claim, chunks: list[Chunk], flags: list[list[str]]
    ) -> None:
        async with self.sessionmaker() as s:
            await s.execute(
                delete(DocumentChunk).where(
                    DocumentChunk.document_id == document_id, DocumentChunk.index_version == claim.index_version
                )
            )
            s.add_all(
                DocumentChunk(
                    id=point_id(document_id, claim.index_version, c.index),
                    document_id=document_id,
                    organization_id=claim.organization_id,
                    index_version=claim.index_version,
                    chunk_index=c.index,
                    kind=c.kind,
                    text=c.text,
                    token_count=c.token_count,
                    page_start=c.page_start,
                    page_end=c.page_end,
                    sheet_name=c.sheet_name,
                    row_start=c.row_start,
                    row_end=c.row_end,
                    section_path=(c.section_path or None) and c.section_path[:1000],
                    content_hash=c.content_hash,
                    flags=flags[i],
                )
                for i, c in enumerate(chunks)
            )
            await s.commit()

    def _payload(self, document_id: uuid.UUID, claim: _Claim, chunk: Chunk, flags: list[str]) -> dict[str, Any]:
        return {
            "org_id": str(claim.organization_id),
            "document_id": str(document_id),
            "index_version": claim.index_version,
            "chunk_id": str(point_id(document_id, claim.index_version, chunk.index)),
            "chunk_index": chunk.index,
            "searchable": False,  # flipped in _finalize
            "acl": [],  # set from current PostgreSQL state in _finalize
            "category_id": str(claim.category_id) if claim.category_id else None,
            "file_type": claim.file_type.value,
            "document_title": claim.title,
            "kind": chunk.kind,
            "page_start": chunk.page_start,
            "page_end": chunk.page_end,
            "sheet_name": chunk.sheet_name,
            "row_start": chunk.row_start,
            "row_end": chunk.row_end,
            "section_path": chunk.section_path,
            "text": chunk.text,
            "token_count": chunk.token_count,
            "content_hash": chunk.content_hash,
            "flags": flags,
        }

    async def _finalize(
        self,
        document_id: uuid.UUID,
        job_id: uuid.UUID,
        claim: _Claim,
        parsed: ParsedDocument,
        chunks: list[Chunk],
        timings: dict[str, float],
    ) -> str:
        version = claim.index_version
        async with self.sessionmaker() as s:
            # Row lock: serializes with concurrent deletes / permission changes on this document.
            document = await s.get(Document, document_id, with_for_update=True)
            job = await s.get(IngestionJob, job_id, with_for_update=True)
            assert document is not None and job is not None
            if document.deleted_at is not None:
                await self._discard_version(s, document_id, version)
                job.status, job.error, job.finished_at = (
                    JobStatus.FAILED,
                    "Document was deleted during processing",
                    _now(),
                )
                await s.commit()
                return "deleted"

            grants = (
                (await s.execute(select(DocumentPermission).where(DocumentPermission.document_id == document_id)))
                .scalars()
                .all()
            )
            await self.store.set_document_payload(
                self.collection,
                document_id,
                {
                    "searchable": True,
                    "acl": document_acl_tokens(document, grants),
                    "category_id": str(document.category_id) if document.category_id else None,
                    "document_title": document.title,
                },
                index_version=version,
            )
            # The new version is live: remove every older version (this and any previous collection).
            await self.store.delete_document(self.collection, document_id, exclude_version=version)
            if claim.previous_config_id is not None and claim.previous_config_id != self.config.id:
                previous = await s.get(EmbeddingConfig, claim.previous_config_id)
                if previous is not None:
                    await self.store.delete_document(previous.qdrant_collection, document_id)
            await s.execute(
                delete(DocumentChunk).where(
                    DocumentChunk.document_id == document_id, DocumentChunk.index_version != version
                )
            )

            document.status = DocumentStatus.INDEXED
            document.active_index_version = version
            document.embedding_config_id = self.config.id
            document.chunk_count = len(chunks)
            document.page_count = parsed.page_count
            document.sheet_names = parsed.sheet_names
            document.doc_metadata = {
                **(document.doc_metadata or {}),
                "extracted": parsed.metadata,
                "warnings": parsed.warnings,
                "flagged_chunks": sum(1 for c in chunks if content_flags(c.text)),
            }
            document.error_message = None
            document.indexed_at = _now()
            job.status = JobStatus.SUCCEEDED
            job.stage = None
            job.finished_at = _now()
            job.metrics = {
                **{k: round(v, 3) for k, v in timings.items()},
                "chunks": len(chunks),
                "tokens": sum(c.token_count for c in chunks),
                "embedding_model": self.embedder.model_id,
                "collection": self.collection,
                "warnings": len(parsed.warnings),
            }
            await s.commit()
        log.info("ingestion_succeeded", document_id=str(document_id), version=version, chunks=len(chunks))
        return "indexed"

    async def _discard_version(self, session: AsyncSession, document_id: uuid.UUID, version: int) -> None:
        await self.store.delete_document(self.collection, document_id, index_version=version)
        await session.execute(
            delete(DocumentChunk).where(
                DocumentChunk.document_id == document_id, DocumentChunk.index_version == version
            )
        )

    async def _cleanup_attempt(self, document_id: uuid.UUID, claim: _Claim) -> None:
        try:
            async with self.sessionmaker() as s:
                await self._discard_version(s, document_id, claim.index_version)
                await s.commit()
        except Exception:  # cleanup must not mask the original error; the next attempt re-cleans
            log.warning("ingestion_cleanup_failed", document_id=str(document_id), version=claim.index_version)

    async def _fail(self, document_id: uuid.UUID, job_id: uuid.UUID, claim: _Claim, code: str, message: str) -> None:
        await self._cleanup_attempt(document_id, claim)
        async with self.sessionmaker() as s:
            document = await s.get(Document, document_id, with_for_update=True)
            job = await s.get(IngestionJob, job_id, with_for_update=True)
            if job is not None:
                job.status, job.error, job.finished_at, job.stage = JobStatus.FAILED, f"{code}: {message}", _now(), None
            if document is not None and document.deleted_at is None:
                if claim.previous_version > 0:
                    # A reprocess failed: the previous version is still indexed and searchable.
                    document.status = DocumentStatus.INDEXED
                    document.error_message = f"Reprocessing failed: {message}"
                else:
                    document.status = DocumentStatus.FAILED
                    document.error_message = message
            await s.commit()
        log.warning("ingestion_failed", document_id=str(document_id), code=code)

    async def _requeue(self, document_id: uuid.UUID, job_id: uuid.UUID, claim: _Claim, message: str) -> None:
        await self._cleanup_attempt(document_id, claim)
        async with self.sessionmaker() as s:
            job = await s.get(IngestionJob, job_id, with_for_update=True)
            if job is not None:
                job.status, job.error, job.stage = JobStatus.QUEUED, message, None
            await s.commit()
