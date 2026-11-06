"""End-to-end ingestion against real PostgreSQL + real Qdrant (TEST_QDRANT_URL).

Embeddings use a deterministic hashing embedder so assertions are exact and fast; the real ONNX
models are exercised in test_real_models.py.
"""

from __future__ import annotations

import os
import uuid
import zlib
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qm
from sqlalchemy import select, update

from app.core.resources import AppResources
from app.models import Document, DocumentChunk, DocumentPermission, IngestionJob
from app.models.enums import DocumentStatus, JobStatus
from app.rag.embeddings.base import SparseVector
from app.rag.ingestion.pipeline import IngestionPipeline, RetryLater
from app.rag.setup import build_ingestion_pipeline
from app.services.access_policy import document_acl_tokens
from app.workers.tasks import purge_document, sweep_jobs, sync_document_payload
from tests.documents import build_docx, build_pdf, build_xlsx
from tests.integration.api_helpers import Session, register, upload_one

TEST_QDRANT_URL = os.environ.get("TEST_QDRANT_URL")
pytestmark = pytest.mark.skipif(not TEST_QDRANT_URL, reason="TEST_QDRANT_URL not set")

DIM = 64


def _tokens(text: str) -> list[str]:
    return [w.strip(".,:;|()").lower() for w in text.split() if w.strip(".,:;|()")]


class HashEmbedder:
    """Deterministic bag-of-words hashing embedder (a real EmbeddingProvider, just not a neural one)."""

    def __init__(self) -> None:
        self.calls = 0
        self.before_call: Callable[[], Any] | None = None

    @property
    def model_id(self) -> str:
        return "test/hash-embedder"

    async def dimension(self) -> int:
        return DIM

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * DIM
        for t in _tokens(text):
            v[zlib.crc32(t.encode()) % DIM] += 1.0
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / norm for x in v]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if self.before_call is not None:
            result = self.before_call()
            if hasattr(result, "__await__"):
                await result
        return [self._vec(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


class HashSparse:
    @property
    def model_id(self) -> str:
        return "test/hash-sparse"

    def _enc(self, text: str) -> SparseVector:
        counts: dict[int, float] = {}
        for t in _tokens(text):
            i = zlib.crc32(t.encode()) % 100_000
            counts[i] = counts.get(i, 0.0) + 1.0
        return SparseVector(indices=list(counts), values=list(counts.values()))

    async def encode_documents(self, texts: list[str]) -> list[SparseVector]:
        return [self._enc(t) for t in texts]

    async def encode_query(self, text: str) -> SparseVector:
        return self._enc(text)


class WordCounter:
    def count(self, text: str) -> int:
        return len(text.split())


# --- fixtures ------------------------------------------------------------------------------


@pytest.fixture
async def qdrant() -> AsyncIterator[AsyncQdrantClient]:
    client = AsyncQdrantClient(url=TEST_QDRANT_URL)
    yield client
    await client.close()


@pytest.fixture
def embedder() -> HashEmbedder:
    return HashEmbedder()


@pytest.fixture
async def pipeline(
    resources: AppResources, qdrant: AsyncQdrantClient, embedder: HashEmbedder
) -> AsyncIterator[IngestionPipeline]:
    tag = uuid.uuid4().hex[:8]
    resources.settings.embedding_model = f"test/hash-{tag}"
    resources.settings.qdrant_collection_alias = f"test_alias_{tag}"
    resources.settings.chunk_size_tokens, resources.settings.chunk_overlap_tokens = 80, 10
    resources.settings.ingestion_max_tries = 3
    p = await build_ingestion_pipeline(
        settings=resources.settings,
        sessionmaker=resources.sessionmaker,
        storage=resources.storage,
        qdrant=qdrant,
        embedder=embedder,
        sparse=HashSparse(),
        token_counter=WordCounter(),
    )
    resources.vector_store = p.store  # the API hides vectors of deleted documents through it
    yield p
    try:
        await qdrant.update_collection_aliases(
            change_aliases_operations=[qm.DeleteAliasOperation(delete_alias=qm.DeleteAlias(alias_name=p.store.alias))]
        )
    except Exception:  # noqa: S110 - alias may not exist
        pass
    await qdrant.delete_collection(p.collection)


@pytest.fixture
def ctx(resources: AppResources, pipeline: IngestionPipeline) -> dict[str, Any]:
    return {
        "settings": resources.settings,
        "sessionmaker": resources.sessionmaker,
        "storage": resources.storage,
        "pipeline": pipeline,
        "store": pipeline.store,
    }


async def _latest_job(resources: AppResources, document_id: str) -> IngestionJob:
    async with resources.sessionmaker() as s:
        return (
            await s.execute(
                select(IngestionJob)
                .where(IngestionJob.document_id == uuid.UUID(document_id))
                .order_by(IngestionJob.created_at.desc(), IngestionJob.id.desc())
                .limit(1)
            )
        ).scalar_one()


async def _run(pipeline: IngestionPipeline, resources: AppResources, document_id: str, attempt: int = 1) -> str:
    job = await _latest_job(resources, document_id)
    return await pipeline.run(uuid.UUID(document_id), job.id, attempt=attempt)


async def _doc(resources: AppResources, document_id: str) -> Document:
    async with resources.sessionmaker() as s:
        doc = await s.get(Document, uuid.UUID(document_id))
        assert doc is not None
        return doc


async def _points(pipeline: IngestionPipeline, document_id: str) -> list[qm.Record]:
    records, _ = await pipeline.store.client.scroll(
        pipeline.collection,
        scroll_filter=qm.Filter(must=[qm.FieldCondition(key="document_id", match=qm.MatchValue(value=document_id))]),
        limit=1000,
        with_payload=True,
    )
    return records


async def _indexed(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources
) -> tuple[Session, dict[str, Any]]:
    admin = await register(client)
    doc = await upload_one(
        admin,
        "Quarterly Report.pdf",
        build_pdf(
            ["Revenue grew in EMEA during the first quarter. " * 6, "The hiring plan adds forty engineers. " * 6],
            table=[["Region", "Revenue"], ["EMEA", "1.2M"]],
        ),
    )
    assert await _run(pipeline, resources, doc["id"]) == "indexed"
    return admin, doc


# --- tests ---------------------------------------------------------------------------------


async def test_pdf_is_indexed_end_to_end(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources
) -> None:
    admin, doc = await _indexed(client, pipeline, resources)

    document = await _doc(resources, doc["id"])
    assert document.status == DocumentStatus.INDEXED
    assert document.active_index_version == 1
    assert document.page_count == 2
    assert document.embedding_config_id == pipeline.config.id
    assert document.doc_metadata["extracted"]["title"] == "Quarterly Report"
    assert document.indexed_at is not None and document.error_message is None

    async with resources.sessionmaker() as s:
        chunks = (
            (await s.execute(select(DocumentChunk).where(DocumentChunk.document_id == document.id))).scalars().all()
        )
        grants = (
            (await s.execute(select(DocumentPermission).where(DocumentPermission.document_id == document.id)))
            .scalars()
            .all()
        )
    assert len(chunks) == document.chunk_count > 0
    assert {c.page_start for c in chunks} == {1, 2}
    assert any(c.kind == "table" and "| EMEA | 1.2M |" in c.text for c in chunks)

    points = await _points(pipeline, doc["id"])
    assert len(points) == len(chunks)
    assert {str(p.id) for p in points} == {str(c.id) for c in chunks}  # PG chunk id == Qdrant point id
    expected_acl = document_acl_tokens(document, grants)
    for p in points:
        assert p.payload["searchable"] is True
        assert p.payload["acl"] == expected_acl
        assert p.payload["org_id"] == admin.org_id
        assert p.payload["index_version"] == 1
        assert p.payload["document_title"] == "Quarterly Report"

    job = await _latest_job(resources, doc["id"])
    assert job.status == JobStatus.SUCCEEDED and job.metrics["chunks"] == len(chunks)

    # Extracted content is available through the API with citation locations.
    resp = await client.get(f"/api/v1/documents/{doc['id']}/chunks", headers=admin.headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "indexed" and body["total"] == len(chunks)
    assert body["items"][0]["page_start"] == 1


async def test_docx_and_excel_keep_sections_sheets_and_rows(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources
) -> None:
    admin = await register(client)
    docx_doc = await upload_one(admin, "policy.docx", build_docx())
    xlsx_doc = await upload_one(admin, "revenue.xlsx", build_xlsx(rows=6))
    assert await _run(pipeline, resources, docx_doc["id"]) == "indexed"
    assert await _run(pipeline, resources, xlsx_doc["id"]) == "indexed"

    docx_points = await _points(pipeline, docx_doc["id"])
    sections = {p.payload["section_path"] for p in docx_points}
    assert "Security Policy > Access Control > Passwords" in sections

    xlsx_points = await _points(pipeline, xlsx_doc["id"])
    revenue = [p.payload for p in xlsx_points if p.payload["sheet_name"] == "Revenue"]
    assert revenue and all(p["kind"] == "rows" and p["row_start"] >= 2 for p in revenue)
    assert {p.payload["sheet_name"] for p in xlsx_points} == {"Revenue", "Notes", "Lookup"}
    doc = await _doc(resources, xlsx_doc["id"])
    assert doc.sheet_names == ["Revenue", "Notes", "Lookup"]


async def test_reprocess_replaces_previous_version(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources
) -> None:
    admin, doc = await _indexed(client, pipeline, resources)
    resp = await client.post(f"/api/v1/documents/{doc['id']}/reprocess", headers=admin.headers)
    assert resp.status_code == 202
    assert await _run(pipeline, resources, doc["id"]) == "indexed"

    document = await _doc(resources, doc["id"])
    assert document.active_index_version == 2
    points = await _points(pipeline, doc["id"])
    assert points and {p.payload["index_version"] for p in points} == {2}
    async with resources.sessionmaker() as s:
        versions = set(
            (
                await s.execute(select(DocumentChunk.index_version).where(DocumentChunk.document_id == document.id))
            ).scalars()
        )
    assert versions == {2}


async def test_failed_reprocess_keeps_previous_version_searchable(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources
) -> None:
    admin, doc = await _indexed(client, pipeline, resources)
    before = await _points(pipeline, doc["id"])
    document = await _doc(resources, doc["id"])
    resources.storage.local_path(document.storage_key).write_bytes(b"%PDF-1.4 corrupted")

    await client.post(f"/api/v1/documents/{doc['id']}/reprocess", headers=admin.headers)
    assert await _run(pipeline, resources, doc["id"]) == "failed"

    document = await _doc(resources, doc["id"])
    assert document.status == DocumentStatus.INDEXED  # still answerable from version 1
    assert document.active_index_version == 1
    assert document.error_message and document.error_message.startswith("Reprocessing failed")
    after = await _points(pipeline, doc["id"])
    assert {str(p.id) for p in after} == {str(p.id) for p in before}
    assert all(p.payload["searchable"] for p in after)
    job = await _latest_job(resources, doc["id"])
    assert job.status == JobStatus.FAILED and job.error.startswith("corrupt_pdf")


async def test_document_without_text_fails_permanently(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources, embedder: HashEmbedder
) -> None:
    admin = await register(client)
    doc = await upload_one(admin, "scan.pdf", build_pdf([""], blank_pages=1))
    assert await _run(pipeline, resources, doc["id"]) == "failed"
    document = await _doc(resources, doc["id"])
    assert document.status == DocumentStatus.FAILED
    assert "No extractable text" in (document.error_message or "")
    assert await _points(pipeline, doc["id"]) == []
    assert embedder.calls == 0  # failed before spending compute on embeddings


async def test_transient_failure_is_retried_then_succeeds(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources, embedder: HashEmbedder
) -> None:
    admin = await register(client)
    doc = await upload_one(admin, "r.pdf", build_pdf(["Some content for retry testing. " * 10]))

    def fail_once() -> None:
        embedder.before_call = None
        raise ConnectionError("model server unavailable")

    embedder.before_call = fail_once
    with pytest.raises(RetryLater) as exc:
        await _run(pipeline, resources, doc["id"], attempt=1)
    assert exc.value.defer_seconds == 30
    job = await _latest_job(resources, doc["id"])
    assert job.status == JobStatus.QUEUED and "Attempt 1 failed" in (job.error or "")
    assert await _points(pipeline, doc["id"]) == []

    assert await _run(pipeline, resources, doc["id"], attempt=2) == "indexed"
    job = await _latest_job(resources, doc["id"])
    assert job.status == JobStatus.SUCCEEDED and job.attempt == 2 and job.index_version == 1


async def test_transient_failures_exhaust_retries(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources, embedder: HashEmbedder
) -> None:
    admin = await register(client)
    doc = await upload_one(admin, "r.pdf", build_pdf(["Content that never embeds. " * 10]))

    def always_fail() -> None:
        raise TimeoutError("still down")

    embedder.before_call = always_fail
    assert await _run(pipeline, resources, doc["id"], attempt=3) == "failed"
    document = await _doc(resources, doc["id"])
    assert document.status == DocumentStatus.FAILED
    assert "after 3 attempts" in (document.error_message or "")


async def test_delete_during_processing_discards_new_version(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources, embedder: HashEmbedder
) -> None:
    admin = await register(client)
    doc = await upload_one(admin, "d.pdf", build_pdf(["Content deleted mid-flight. " * 10]))

    async def delete_now() -> None:
        async with resources.sessionmaker() as s:
            await s.execute(
                update(Document).where(Document.id == uuid.UUID(doc["id"])).values(deleted_at=datetime.now(UTC))
            )
            await s.commit()

    embedder.before_call = delete_now
    assert await _run(pipeline, resources, doc["id"]) == "deleted"
    assert await _points(pipeline, doc["id"]) == []
    async with resources.sessionmaker() as s:
        assert not (
            await s.execute(select(DocumentChunk.id).where(DocumentChunk.document_id == uuid.UUID(doc["id"])))
        ).first()


async def test_rerunning_a_finished_job_is_a_no_op(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources, embedder: HashEmbedder
) -> None:
    _, doc = await _indexed(client, pipeline, resources)
    calls = embedder.calls
    assert await _run(pipeline, resources, doc["id"]) == "already_finished"
    assert embedder.calls == calls


async def test_permission_change_syncs_acl_to_qdrant(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources, ctx: dict[str, Any]
) -> None:
    admin, doc = await _indexed(client, pipeline, resources)
    org = admin.org_id
    assert f"org:{org}" in (await _points(pipeline, doc["id"]))[0].payload["acl"]

    group = (await client.post("/api/v1/groups", headers=admin.headers, json={"name": "Finance"})).json()
    await client.patch(f"/api/v1/documents/{doc['id']}", headers=admin.headers, json={"visibility": "restricted"})
    await client.put(
        f"/api/v1/documents/{doc['id']}/permissions",
        headers=admin.headers,
        json={"grants": [{"principal_type": "group", "principal_id": group["id"]}]},
    )
    queued = [j for j in resources.job_queue.jobs if j[0] == "sync_document_payload"]  # type: ignore[attr-defined]
    assert len(queued) == 2  # one per change

    assert await sync_document_payload(ctx, doc["id"]) == "synced"
    acl = (await _points(pipeline, doc["id"]))[0].payload["acl"]
    assert f"org:{org}" not in acl  # no longer visible to the whole organization
    assert f"group:{org}:{group['id']}" in acl
    assert f"admin:{org}" in acl and f"user:{org}:{admin.user_id}" in acl


async def test_delete_hides_then_purge_removes_vectors(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources, ctx: dict[str, Any]
) -> None:
    admin, doc = await _indexed(client, pipeline, resources)
    assert (await client.delete(f"/api/v1/documents/{doc['id']}", headers=admin.headers)).status_code == 202
    # Hidden immediately (API), deleted by the purge job (worker).
    assert all(not p.payload["searchable"] for p in await _points(pipeline, doc["id"]))
    assert await purge_document(ctx, doc["id"]) == "purged"
    assert await _points(pipeline, doc["id"]) == []
    document = await _doc(resources, doc["id"])
    assert not resources.storage.local_path(document.storage_key).exists()


class _RecordingRedis:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[str, ...], str]] = []

    async def enqueue_job(self, function: str, *args: str, _job_id: str) -> object:
        self.jobs.append((function, args, _job_id))
        return object()


async def test_sweep_requeues_lost_and_stuck_jobs(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources, ctx: dict[str, Any]
) -> None:
    admin = await register(client)
    lost = await upload_one(admin, "lost.pdf", build_pdf(["Lost job. " * 5]))
    stuck = await upload_one(admin, "stuck.pdf", build_pdf(["Stuck job. " * 5]))
    fresh = await upload_one(admin, "fresh.pdf", build_pdf(["Fresh job. " * 5]))
    old = datetime.now(UTC) - timedelta(hours=2)
    async with resources.sessionmaker() as s:
        await s.execute(
            update(IngestionJob).where(IngestionJob.document_id == uuid.UUID(lost["id"])).values(created_at=old)
        )
        await s.execute(
            update(IngestionJob)
            .where(IngestionJob.document_id == uuid.UUID(stuck["id"]))
            .values(status=JobStatus.RUNNING, started_at=old, created_at=old)
        )
        await s.commit()

    redis = _RecordingRedis()
    result = await sweep_jobs({**ctx, "redis": redis})
    assert result == {"requeued": 2, "recovered_running": 1}
    requeued_docs = {args[0] for _, args, _ in redis.jobs}
    assert requeued_docs == {lost["id"], stuck["id"]}
    assert fresh["id"] not in requeued_docs
    assert (await _latest_job(resources, stuck["id"])).status == JobStatus.QUEUED
