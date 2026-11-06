"""Background tasks executed by the arq worker."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qm

from app.repositories.documents import DocumentRepository

log = structlog.get_logger(__name__)


async def delete_document_vectors(qdrant: AsyncQdrantClient, collection: str, document_id: uuid.UUID) -> None:
    if not await qdrant.collection_exists(collection):
        return
    await qdrant.delete(
        collection_name=collection,
        points_selector=qm.FilterSelector(
            filter=qm.Filter(must=[qm.FieldCondition(key="document_id", match=qm.MatchValue(value=str(document_id)))])
        ),
        wait=True,
    )


async def purge_document(ctx: dict[str, Any], document_id: str) -> str:
    """Remove the stored file and all vectors of a soft-deleted document. Idempotent."""
    settings = ctx["settings"]
    doc_uuid = uuid.UUID(document_id)
    async with ctx["sessionmaker"]() as session:
        document = await DocumentRepository(session).get_any(doc_uuid)
        if document is None:
            return "missing"
        if document.deleted_at is None:
            # Never purge a live document, whatever the queue says.
            log.warning("purge_skipped_not_deleted", document_id=document_id)
            return "not_deleted"
        if document.purged_at is not None:
            return "already_purged"

        await delete_document_vectors(ctx["qdrant"], settings.qdrant_collection_alias, doc_uuid)
        await ctx["storage"].delete(document.storage_key)
        document.purged_at = datetime.now(UTC)
        await session.commit()

    log.info("document_purged", document_id=document_id)
    return "purged"
