from __future__ import annotations

import uuid

import httpx
from sqlalchemy import select

from app.core.resources import AppResources
from app.models import Document
from app.workers.tasks import purge_document
from tests.helpers import pdf_bytes
from tests.integration.api_helpers import register, upload_one


class RecordingStore:
    """Vector-store stand-in recording purges (real Qdrant purge: test_ingestion_pipeline.py)."""

    def __init__(self) -> None:
        self.purged: list[uuid.UUID] = []

    async def delete_document_everywhere(self, document_id: uuid.UUID) -> None:
        self.purged.append(document_id)


async def test_purge_removes_file_only_for_deleted_documents(
    client: httpx.AsyncClient, resources: AppResources
) -> None:
    admin = await register(client)
    live = await upload_one(admin, "live.pdf", pdf_bytes("live"))
    gone = await upload_one(admin, "gone.pdf", pdf_bytes("gone"))
    await client.delete(f"/api/v1/documents/{gone['id']}", headers=admin.headers)

    store = RecordingStore()
    ctx = {
        "settings": resources.settings,
        "sessionmaker": resources.sessionmaker,
        "storage": resources.storage,
        "store": store,
    }
    assert await purge_document(ctx, live["id"]) == "not_deleted"
    assert await purge_document(ctx, gone["id"]) == "purged"
    assert await purge_document(ctx, gone["id"]) == "already_purged"
    assert await purge_document(ctx, str(uuid.uuid4())) == "missing"
    assert store.purged == [uuid.UUID(gone["id"])]

    async with resources.sessionmaker() as s:
        docs = {d.title: d for d in (await s.execute(select(Document))).scalars()}
    assert resources.storage.local_path(docs["live"].storage_key).is_file()
    assert not resources.storage.local_path(docs["gone"].storage_key).exists()
    assert docs["gone"].purged_at is not None
