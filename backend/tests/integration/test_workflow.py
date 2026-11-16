"""The n8n integration end to end against real PostgreSQL and Qdrant.

A fake n8n receives the upload webhook (recording it) and the tests then call the internal API exactly
as the workflow does: process -> poll status -> wait/retry -> record the outcome. The ingestion pipeline
itself is the real one (deterministic embeddings), so statuses, chunks and vectors are real.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from qdrant_client.http.exceptions import ResponseHandlingException
from sqlalchemy import func, select, update

from app.core.resources import AppResources
from app.models import Document, DocumentChunk, IngestionJob
from app.models.enums import DocumentStatus, JobStatus
from app.rag.ingestion.pipeline import IngestionPipeline
from app.services.job_queue import INGEST_DOCUMENT
from app.services.workflow import UploadEvent
from app.workers.tasks import sweep_jobs
from tests.documents import build_docx, build_pdf, build_xlsx
from tests.integration.api_helpers import Session, add_member, register, upload_one
from tests.integration.conftest import RecordingQueue
from tests.integration.rag_helpers import latest_job, run_ingestion

KEY = "internal-api-key-0123456789-abcdef"
SECRET = "webhook-secret-0123456789-abcdef"
HEADERS = {"X-Internal-Api-Key": KEY}


class FakeN8n:
    """Stands in for n8n's webhook: records events; ``accept=False`` behaves like an inactive workflow."""

    def __init__(self) -> None:
        self.events: list[UploadEvent] = []
        self.accept = True

    async def document_uploaded(self, event: UploadEvent) -> bool:
        self.events.append(event)
        return self.accept


@pytest.fixture
def settings_overrides() -> dict[str, Any]:
    return {
        "n8n_enabled": True,
        "internal_api_key": KEY,
        "n8n_webhook_secret": SECRET,
        "n8n_max_retries": 3,
        "n8n_retry_base_seconds": 30,
        "n8n_retry_max_seconds": 600,
        "n8n_stall_minutes": 30,
    }


@pytest.fixture
def n8n(resources: AppResources) -> FakeN8n:
    fake = FakeN8n()
    resources.workflow_notifier = fake
    return fake


async def _process(s: Session, event: UploadEvent, **override: Any) -> httpx.Response:
    body = {"job_id": str(event.job_id), "organization_id": s.org_id, "event_id": event.event_id, **override}
    return await s.client.post(f"/internal/documents/{event.document_id}/process", headers=HEADERS, json=body)


async def _status(s: Session, job_id: uuid.UUID | str, org: str | None = None) -> httpx.Response:
    return await s.client.get(
        f"/internal/jobs/{job_id}/status", headers=HEADERS, params={"organization_id": org or s.org_id}
    )


async def _retry(s: Session, job_id: uuid.UUID | str, org: str | None = None) -> httpx.Response:
    return await s.client.post(
        f"/internal/jobs/{job_id}/retry", headers=HEADERS, json={"organization_id": org or s.org_id}
    )


async def _record(s: Session, job_id: uuid.UUID | str, state: str, message: str | None = None) -> httpx.Response:
    return await s.client.post(
        f"/internal/jobs/{job_id}/workflow-status",
        headers=HEADERS,
        json={"organization_id": s.org_id, "state": state, "message": message, "execution_id": "exec-1"},
    )


async def _document(resources: AppResources, document_id: str | uuid.UUID) -> Document:
    async with resources.sessionmaker() as s:
        return (await s.execute(select(Document).where(Document.id == uuid.UUID(str(document_id))))).scalar_one()


async def _job(resources: AppResources, job_id: uuid.UUID) -> IngestionJob:
    async with resources.sessionmaker() as s:
        return (await s.execute(select(IngestionJob).where(IngestionJob.id == job_id))).scalar_one()


async def _chunks(resources: AppResources, document_id: str) -> int:
    async with resources.sessionmaker() as s:
        return int(
            await s.scalar(
                select(func.count())
                .select_from(DocumentChunk)
                .where(DocumentChunk.document_id == uuid.UUID(document_id))
            )
            or 0
        )


async def _upload(s: Session, name: str, data: bytes, n8n: FakeN8n) -> tuple[dict[str, Any], UploadEvent]:
    before = len(n8n.events)
    doc = await upload_one(s, name, data)
    assert len(n8n.events) == before + 1
    return doc, n8n.events[-1]


def _fail_vector_store(pipeline: IngestionPipeline, monkeypatch: pytest.MonkeyPatch) -> None:
    async def down(*args: Any, **kwargs: Any) -> None:
        raise ResponseHandlingException(Exception("qdrant is down"))

    monkeypatch.setattr(pipeline.store, "upsert", down)


# --- the happy path, for every format ------------------------------------------------------------------------------

DOCUMENTS = {
    "pdf": ("Quarterly Report.pdf", lambda: build_pdf(["Revenue grew in EMEA during the first quarter. " * 6])),
    "docx": ("Security Policy.docx", lambda: build_docx()),
    "xlsx": ("Revenue 2026.xlsx", lambda: build_xlsx()),
}


@pytest.mark.parametrize("kind", list(DOCUMENTS))
async def test_upload_webhook_process_poll_and_complete(
    kind: str,
    client: httpx.AsyncClient,
    pipeline: IngestionPipeline,
    resources: AppResources,
    queue: RecordingQueue,
    n8n: FakeN8n,
) -> None:
    admin = await register(client)
    name, build = DOCUMENTS[kind]
    doc, event = await _upload(admin, name, build(), n8n)

    # The upload only announced itself: metadata in the webhook, nothing queued directly.
    assert queue.jobs == []
    assert (event.document_id, event.organization_id, event.file_type) == (
        uuid.UUID(doc["id"]),
        uuid.UUID(admin.org_id),
        kind,
    )
    assert doc["status"] == "uploaded"
    assert event.title is None  # titles can be sensitive and n8n stores execution data: off by default
    job = await _job(resources, event.job_id)
    assert (job.workflow_managed, job.workflow_status, job.status) == (True, "triggered", JobStatus.QUEUED)

    # n8n asks FastAPI to start processing: 202 and a queued ingest job, returned at once.
    resp = await _process(admin, event, execution_id="exec-1")
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert (body["started"], body["state"], body["document_status"]) == (True, "queued", "queued")
    assert [j[0] for j in queue.jobs] == [INGEST_DOCUMENT]
    assert (await _status(admin, event.job_id)).json()["state"] == "queued"

    # The worker runs the real pipeline; the workflow polls until it is done.
    assert await run_ingestion(pipeline, resources, doc["id"]) == "indexed"
    status = (await _status(admin, event.job_id)).json()
    assert (status["state"], status["document_status"], status["can_retry"], status["error_code"]) == (
        "succeeded",
        "indexed",
        False,
        None,
    )
    assert (await _record(admin, event.job_id, "succeeded")).status_code == 200
    document = await _document(resources, doc["id"])
    assert document.status == DocumentStatus.INDEXED and document.chunk_count > 0
    assert (await _job(resources, event.job_id)).workflow_status == "succeeded"

    # The dashboard sees the same job.
    listed = (await client.get("/api/v1/documents", headers=admin.headers)).json()["items"][0]
    assert listed["status"] == "indexed" and listed["processing"]["state"] == "succeeded"
    assert listed["processing"]["workflow_managed"] is True


async def test_duplicate_process_calls_and_webhooks_do_not_duplicate_work(
    client: httpx.AsyncClient,
    pipeline: IngestionPipeline,
    resources: AppResources,
    queue: RecordingQueue,
    n8n: FakeN8n,
) -> None:
    admin = await register(client)
    doc, event = await _upload(admin, *DOCUMENTS["pdf"][:1], DOCUMENTS["pdf"][1](), n8n)

    first, second, third = [await _process(admin, event) for _ in range(3)]
    assert (first.status_code, first.json()["started"]) == (202, True)
    assert (second.status_code, second.json()["started"]) == (200, False)
    assert (third.status_code, third.json()["started"]) == (200, False)
    assert len(queue.jobs) == 1  # exactly one run was queued

    assert await run_ingestion(pipeline, resources, doc["id"]) == "indexed"
    chunks = await _chunks(resources, doc["id"])
    # A process call after completion changes nothing either.
    late = await _process(admin, event)
    assert (late.status_code, late.json()["started"], late.json()["state"]) == (200, False, "succeeded")
    assert len(queue.jobs) == 1 and await _chunks(resources, doc["id"]) == chunks


# --- authentication and organization isolation ----------------------------------------------------------------------


async def test_internal_api_rejects_missing_and_wrong_keys(
    client: httpx.AsyncClient, resources: AppResources, n8n: FakeN8n
) -> None:
    admin = await register(client)
    doc, event = await _upload(admin, DOCUMENTS["pdf"][0], DOCUMENTS["pdf"][1](), n8n)
    body = {"job_id": str(event.job_id), "organization_id": admin.org_id}
    url = f"/internal/documents/{doc['id']}/process"
    for headers in ({}, {"X-Internal-Api-Key": "wrong"}, {"Authorization": f"Bearer {admin.token}"}):
        resp = await client.post(url, headers=headers, json=body)
        assert resp.status_code == 401, headers
        assert resp.json()["error"]["code"] == "not_authenticated"
    assert (
        await client.get(f"/internal/jobs/{event.job_id}/status", params={"organization_id": admin.org_id})
    ).status_code == 401
    # A user's own token is no substitute for the key, and nothing was started.
    assert (await _job(resources, event.job_id)).workflow_status == "triggered"


async def test_internal_api_does_not_exist_when_n8n_is_off(client: httpx.AsyncClient, resources: AppResources) -> None:
    resources.settings.n8n_enabled = False
    resp = await client.post(
        f"/internal/documents/{uuid.uuid4()}/process",
        headers=HEADERS,
        json={"job_id": str(uuid.uuid4()), "organization_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404


async def test_other_organizations_cannot_touch_a_job(
    client: httpx.AsyncClient, make_client: Any, resources: AppResources, n8n: FakeN8n
) -> None:
    admin = await register(client)
    outsider = await register(make_client(), org="Other Corp")
    _, event = await _upload(admin, DOCUMENTS["pdf"][0], DOCUMENTS["pdf"][1](), n8n)

    # Naming the wrong organization makes the job "not found" for every operation.
    for resp in (
        await _process(admin, event, organization_id=outsider.org_id),
        await _status(admin, event.job_id, org=outsider.org_id),
        await _retry(admin, event.job_id, org=outsider.org_id),
    ):
        assert resp.status_code == 404 and resp.json()["error"]["code"] == "job_not_found"
    bad = await admin.client.post(
        f"/internal/jobs/{event.job_id}/workflow-status",
        headers=HEADERS,
        json={"organization_id": outsider.org_id, "state": "failed"},
    )
    assert bad.status_code == 404
    assert (await _job(resources, event.job_id)).workflow_status == "triggered"  # untouched


async def test_invalid_events_are_rejected(client: httpx.AsyncClient, resources: AppResources, n8n: FakeN8n) -> None:
    admin = await register(client)
    doc, event = await _upload(admin, DOCUMENTS["pdf"][0], DOCUMENTS["pdf"][1](), n8n)
    url = f"/internal/documents/{doc['id']}/process"
    ok = {"job_id": str(event.job_id), "organization_id": admin.org_id}

    assert (
        await client.post(url, headers=HEADERS, json={"organization_id": admin.org_id})
    ).status_code == 422  # no job
    assert (await client.post(url, headers=HEADERS, json={"job_id": str(event.job_id)})).status_code == 422  # no org
    assert (await client.post(url, headers=HEADERS, json={**ok, "job_id": "not-a-uuid"})).status_code == 422
    assert (await client.post(url, headers=HEADERS, json={**ok, "extra": 1})).status_code == 422
    assert (await client.post(url, headers=HEADERS, json={**ok, "job_id": str(uuid.uuid4())})).status_code == 404
    # A job of another document does not match this document id.
    other, _ = await _upload(admin, "Other.pdf", build_pdf(["Different content. " * 6]), n8n)
    mismatch = await client.post(
        f"/internal/documents/{other['id']}/process",
        headers=HEADERS,
        json={"job_id": str(event.job_id), "organization_id": admin.org_id},
    )
    assert mismatch.status_code == 404
    assert (
        await client.post(
            f"/internal/jobs/{event.job_id}/workflow-status",
            headers=HEADERS,
            json={"organization_id": admin.org_id, "state": "bogus"},
        )
    ).status_code == 422
    assert (await _job(resources, event.job_id)).workflow_status == "triggered"


async def test_deleted_documents_are_not_processed(
    client: httpx.AsyncClient, queue: RecordingQueue, n8n: FakeN8n
) -> None:
    admin = await register(client)
    doc, event = await _upload(admin, DOCUMENTS["pdf"][0], DOCUMENTS["pdf"][1](), n8n)
    assert (await client.delete(f"/api/v1/documents/{doc['id']}", headers=admin.headers)).status_code == 202
    resp = await _process(admin, event)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "document_deleted"
    assert queue.jobs == [] or all(j[0] != INGEST_DOCUMENT for j in queue.jobs)


# --- failures, retries, exhaustion ----------------------------------------------------------------------------------


async def test_temporary_failure_is_retried_with_backoff_then_recovers_without_duplicates(
    client: httpx.AsyncClient,
    pipeline: IngestionPipeline,
    resources: AppResources,
    queue: RecordingQueue,
    n8n: FakeN8n,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await register(client)
    doc, event = await _upload(admin, DOCUMENTS["pdf"][0], DOCUMENTS["pdf"][1](), n8n)
    await _process(admin, event)

    # Attempt 1: Qdrant is down. The worker does NOT retry on its own; it reports a temporary failure.
    with monkeypatch.context() as m:
        _fail_vector_store(pipeline, m)
        assert await run_ingestion(pipeline, resources, doc["id"], attempt=1) == "failed"
    status = (await _status(admin, event.job_id)).json()
    assert (status["state"], status["retryable"], status["can_retry"], status["error_code"]) == (
        "failed",
        True,
        True,
        "vector_store_unavailable",
    )
    assert status["retry_after_seconds"] == 30 and status["document_status"] == "retrying"
    assert "qdrant" not in (status["error"] or "").lower()  # a code, not exception text
    assert await _chunks(resources, doc["id"]) == 0  # the failed attempt left nothing behind

    # n8n waits the advised delay and retries: 202 and a new queued run.
    queued_before = len(queue.jobs)
    resp = await _retry(admin, event.job_id)
    assert (resp.status_code, resp.json()["retried"], resp.json()["retry_count"]) == (202, True, 1)
    assert len(queue.jobs) == queued_before + 1
    # A duplicate retry delivery changes nothing.
    again = await _retry(admin, event.job_id)
    assert (again.status_code, again.json()["retried"]) == (200, False) and len(queue.jobs) == queued_before + 1

    # Attempt 2 succeeds: the document is completed, with exactly one set of chunks.
    assert await run_ingestion(pipeline, resources, doc["id"], attempt=1) == "indexed"
    document = await _document(resources, doc["id"])
    assert document.status == DocumentStatus.INDEXED and document.error_message is None
    assert await _chunks(resources, doc["id"]) == document.chunk_count > 0
    final = (await _status(admin, event.job_id)).json()
    assert (final["state"], final["retry_count"], final["error_code"]) == ("succeeded", 1, None)
    # Vectors are not duplicated either.
    assert resources.qdrant is not None
    assert (await resources.qdrant.count(pipeline.collection)).count == document.chunk_count


async def test_retries_stop_at_the_maximum_and_the_document_fails(
    client: httpx.AsyncClient,
    pipeline: IngestionPipeline,
    resources: AppResources,
    n8n: FakeN8n,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await register(client)
    doc, event = await _upload(admin, DOCUMENTS["pdf"][0], DOCUMENTS["pdf"][1](), n8n)
    await _process(admin, event)
    _fail_vector_store(pipeline, monkeypatch)

    delays = []
    for _ in range(4):  # first try + 3 retries
        assert await run_ingestion(pipeline, resources, doc["id"], attempt=1) == "failed"
        status = (await _status(admin, event.job_id)).json()
        if not status["can_retry"]:
            break
        delays.append(status["retry_after_seconds"])
        assert (await _retry(admin, event.job_id)).status_code == 202
    assert delays == [30, 60, 120]  # exponential, from the settings

    # Out of retries: no further retry is accepted, and nothing loops.
    exhausted = (await _status(admin, event.job_id)).json()
    assert (exhausted["retry_count"], exhausted["can_retry"], exhausted["max_retries"]) == (3, False, 3)
    refused = await _retry(admin, event.job_id)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "retries_exhausted"

    # The workflow reports it; PostgreSQL ends in FAILED with a useful message.
    resp = await _record(admin, event.job_id, "exhausted")
    assert resp.status_code == 200 and resp.json()["document_status"] == "failed"
    document = await _document(resources, doc["id"])
    assert document.status == DocumentStatus.FAILED
    assert "after 3 retries" in (document.error_message or "") and "vector_store_unavailable" in document.error_message
    job = await _job(resources, event.job_id)
    assert (job.workflow_status, job.retryable, job.error_code) == ("failed", False, "vector_store_unavailable")
    assert await _chunks(resources, doc["id"]) == 0
    # Reporting twice is harmless.
    assert (await _record(admin, event.job_id, "exhausted")).status_code == 200


async def test_embedding_provider_unavailable_is_a_temporary_network_failure(
    client: httpx.AsyncClient,
    pipeline: IngestionPipeline,
    resources: AppResources,
    n8n: FakeN8n,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await register(client)
    doc, event = await _upload(admin, DOCUMENTS["docx"][0], DOCUMENTS["docx"][1](), n8n)
    await _process(admin, event)

    async def unreachable(*args: Any, **kwargs: Any) -> Any:
        raise httpx.ConnectError("embedding provider unreachable")

    monkeypatch.setattr(pipeline.embedder, "embed_documents", unreachable)
    assert await run_ingestion(pipeline, resources, doc["id"]) == "failed"
    status = (await _status(admin, event.job_id)).json()
    assert (status["error_code"], status["can_retry"]) == ("network_error", True)


async def test_permanent_failures_are_not_retried(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources, n8n: FakeN8n
) -> None:
    admin = await register(client)
    doc, event = await _upload(admin, DOCUMENTS["pdf"][0], DOCUMENTS["pdf"][1](), n8n)
    await _process(admin, event)
    document = await _document(resources, doc["id"])
    resources.storage.local_path(document.storage_key).write_bytes(b"%PDF-1.4 corrupted")  # unreadable

    assert await run_ingestion(pipeline, resources, doc["id"]) == "failed"
    status = (await _status(admin, event.job_id)).json()
    assert (status["state"], status["retryable"], status["can_retry"], status["error_code"]) == (
        "failed",
        False,
        False,
        "corrupt_pdf",
    )
    assert status["document_status"] == "failed"  # already final: no waiting for a retry
    refused = await _retry(admin, event.job_id)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "not_retryable"
    # The workflow records the permanent failure (idempotent).
    assert (await _record(admin, event.job_id, "failed", "corrupt")).status_code == 200


async def test_success_cannot_be_claimed_by_the_workflow(
    client: httpx.AsyncClient, resources: AppResources, n8n: FakeN8n
) -> None:
    admin = await register(client)
    doc, event = await _upload(admin, DOCUMENTS["pdf"][0], DOCUMENTS["pdf"][1](), n8n)
    await _process(admin, event)
    # PostgreSQL decides: a queued job is not a success, and a running/queued job cannot be failed by n8n.
    assert (await _record(admin, event.job_id, "succeeded")).json()["error"]["code"] == "job_not_succeeded"
    assert (await _record(admin, event.job_id, "failed")).json()["error"]["code"] == "job_in_progress"
    assert (await _document(resources, doc["id"])).status == DocumentStatus.QUEUED


# --- manual retry, fallback and safety nets ----------------------------------------------------------------


async def test_failed_documents_can_be_retried_manually_through_the_workflow(
    client: httpx.AsyncClient,
    pipeline: IngestionPipeline,
    resources: AppResources,
    n8n: FakeN8n,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await register(client)
    doc, event = await _upload(admin, DOCUMENTS["pdf"][0], DOCUMENTS["pdf"][1](), n8n)
    await _process(admin, event)
    _fail_vector_store(pipeline, monkeypatch)
    assert await run_ingestion(pipeline, resources, doc["id"]) == "failed"

    # While a retry is pending, a manual reprocess is refused (it would race the pending retry).
    pending = await client.post(f"/api/v1/documents/{doc['id']}/reprocess", headers=admin.headers)
    assert pending.status_code == 409 and pending.json()["error"]["code"] == "reprocess_in_progress"

    await _record(admin, event.job_id, "exhausted")
    assert (await _document(resources, doc["id"])).status == DocumentStatus.FAILED

    # Now the user retries: a new managed job, announced to n8n again.
    events_before = len(n8n.events)
    monkeypatch.undo()
    resp = await client.post(f"/api/v1/documents/{doc['id']}/reprocess", headers=admin.headers)
    assert resp.status_code == 202, resp.text
    assert len(n8n.events) == events_before + 1 and n8n.events[-1].job_id != event.job_id
    # No stale "failed" while the workflow starts the new job.
    assert (await _document(resources, doc["id"])).status == DocumentStatus.QUEUED
    new_event = n8n.events[-1]
    assert (await _process(admin, new_event)).status_code == 202
    assert await run_ingestion(pipeline, resources, doc["id"]) == "indexed"
    assert (await _document(resources, doc["id"])).status == DocumentStatus.INDEXED


async def test_webhook_failure_falls_back_to_the_worker(
    client: httpx.AsyncClient,
    pipeline: IngestionPipeline,
    resources: AppResources,
    queue: RecordingQueue,
    n8n: FakeN8n,
) -> None:
    n8n.accept = False  # n8n down or the workflow inactive
    admin = await register(client)
    doc = await upload_one(admin, *DOCUMENTS["pdf"][:1], DOCUMENTS["pdf"][1]())
    job = await latest_job(resources, doc["id"])
    assert (job.workflow_managed, job.workflow_status) == (False, None)  # the worker owns it now
    assert [j[0] for j in queue.jobs] == [INGEST_DOCUMENT]
    assert await run_ingestion(pipeline, resources, doc["id"]) == "indexed"  # processed without n8n


async def test_a_notifier_that_raises_never_breaks_the_upload(
    client: httpx.AsyncClient, resources: AppResources, queue: RecordingQueue
) -> None:
    class Exploding:
        async def document_uploaded(self, event: UploadEvent) -> bool:
            raise RuntimeError("boom")

    resources.workflow_notifier = Exploding()  # type: ignore[assignment]
    admin = await register(client)
    doc = await upload_one(admin, *DOCUMENTS["pdf"][:1], DOCUMENTS["pdf"][1]())
    assert [j[0] for j in queue.jobs] == [INGEST_DOCUMENT]
    assert (await latest_job(resources, doc["id"])).workflow_managed is False


async def test_without_n8n_uploads_are_queued_directly_as_before(
    client: httpx.AsyncClient, resources: AppResources, queue: RecordingQueue
) -> None:
    resources.settings.n8n_enabled = False
    admin = await register(client)
    doc = await upload_one(admin, *DOCUMENTS["pdf"][:1], DOCUMENTS["pdf"][1]())
    assert [j[0] for j in queue.jobs] == [INGEST_DOCUMENT]
    job = await latest_job(resources, doc["id"])
    assert (job.workflow_managed, job.workflow_status) == (False, None)


async def test_sweep_takes_back_lost_events_and_fails_stalled_retries(
    client: httpx.AsyncClient,
    pipeline: IngestionPipeline,
    resources: AppResources,
    n8n: FakeN8n,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await register(client)
    lost_doc, lost = await _upload(admin, "Lost.pdf", build_pdf(["Never processed by n8n. " * 6]), n8n)
    stalled_doc, stalled = await _upload(admin, "Stalled.pdf", build_pdf(["Retry never came. " * 6]), n8n)

    # One job's event was lost (n8n never called back); the other failed and its workflow disappeared.
    await _process(admin, stalled)
    _fail_vector_store(pipeline, monkeypatch)
    assert await run_ingestion(pipeline, resources, stalled_doc["id"]) == "failed"
    monkeypatch.undo()
    old = datetime.now(UTC) - timedelta(hours=2)
    async with resources.sessionmaker() as s:
        await s.execute(update(IngestionJob).where(IngestionJob.id == lost.job_id).values(created_at=old))
        await s.execute(update(IngestionJob).where(IngestionJob.id == stalled.job_id).values(workflow_updated_at=old))
        await s.commit()

    class Redis:
        def __init__(self) -> None:
            self.enqueued: list[tuple[Any, ...]] = []

        async def enqueue_job(self, *args: Any, **kwargs: Any) -> object:
            self.enqueued.append(args)
            return object()

    redis = Redis()
    ctx = {"settings": resources.settings, "sessionmaker": resources.sessionmaker, "redis": redis}
    result = await sweep_jobs(ctx)
    assert result["requeued"] == 1 and redis.enqueued[0][1] == lost_doc["id"]
    assert (await _job(resources, lost.job_id)).workflow_managed is False  # the worker owns it now
    stalled_job = await _job(resources, stalled.job_id)
    assert (stalled_job.workflow_status, stalled_job.retryable) == ("failed", False)
    document = await _document(resources, stalled_doc["id"])
    assert document.status == DocumentStatus.FAILED and "stopped responding" in (document.error_message or "")


async def test_other_users_still_cannot_see_processing_of_private_documents(
    client: httpx.AsyncClient, make_client: Any, n8n: FakeN8n
) -> None:
    admin = await register(client)
    alice = await add_member(admin, make_client())
    doc = await upload_one(admin, *DOCUMENTS["pdf"][:1], DOCUMENTS["pdf"][1](), visibility="private")
    listed = (await alice.client.get("/api/v1/documents", headers=alice.headers)).json()["items"]
    assert all(d["id"] != doc["id"] for d in listed)


async def test_titles_are_sent_only_when_enabled(
    client: httpx.AsyncClient, resources: AppResources, n8n: FakeN8n
) -> None:
    resources.settings.n8n_include_title_in_events = True
    admin = await register(client)
    _, event = await _upload(admin, "Quarterly Report.pdf", DOCUMENTS["pdf"][1](), n8n)
    assert event.title == "Quarterly Report"
