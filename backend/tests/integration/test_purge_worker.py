from __future__ import annotations

import uuid

import httpx
from sqlalchemy import select

from app.core.resources import AppResources
from app.models import Document
from app.workers.tasks import purge_document
from tests.helpers import pdf_bytes
from tests.integration.api_helpers import register, upload_one


class EmptyQdrant:
    """Qdrant stand-in with no collections (vector deletion is covered with real Qdrant in Phase 3)."""

    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def collection_exists(self, name: str) -> bool:
        return False


async def test_purge_removes_file_only_for_deleted_documents(
    client: httpx.AsyncClient, resources: AppResources
) -> None:
    admin = await register(client)
    live = await upload_one(admin, "live.pdf", pdf_bytes("live"))
    gone = await upload_one(admin, "gone.pdf", pdf_bytes("gone"))
    await client.delete(f"/api/v1/documents/{gone['id']}", headers=admin.headers)

    ctx = {
        "settings": resources.settings,
        "sessionmaker": resources.sessionmaker,
        "storage": resources.storage,
        "qdrant": EmptyQdrant(),
    }
    assert await purge_document(ctx, live["id"]) == "not_deleted"
    assert await purge_document(ctx, gone["id"]) == "purged"
    assert await purge_document(ctx, gone["id"]) == "already_purged"
    assert await purge_document(ctx, str(uuid.uuid4())) == "missing"

    async with resources.sessionmaker() as s:
        docs = {d.title: d for d in (await s.execute(select(Document))).scalars()}
    assert resources.storage.local_path(docs["live"].storage_key).is_file()
    assert not resources.storage.local_path(docs["gone"].storage_key).exists()
    assert docs["gone"].purged_at is not None
