"""Qdrant access for chunk vectors.

Each embedding config has its own collection (``dm_<model>_<chunking>_<hash>``); the alias
``QDRANT_COLLECTION_ALIAS`` points at the active one for retrieval.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import structlog
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qm

from app.rag.embeddings.base import SparseVector
from app.utils.text import slugify

log = structlog.get_logger(__name__)

COLLECTION_PREFIX = "dm_"
DENSE = "dense"
SPARSE = "bm25"
_UPSERT_BATCH = 128

# Payload fields used in filters → index type.
_KEYWORD_FIELDS = ("document_id", "acl", "category_id", "file_type")
_INTEGER_FIELDS = ("index_version",)


def collection_name(embedding_model: str, sparse_model: str, chunk_size: int, chunk_overlap: int) -> str:
    """Short, readable and unique per configuration, e.g. ``dm_bge-base-en-v1-5_400-60_1a2b3c4d``.

    Kept short on purpose: Qdrant nests several directory levels below the collection directory,
    and some platforms (Windows without long-path support) cap total path length at 260.
    """
    digest = hashlib.sha256(f"{embedding_model}|{sparse_model}|{chunk_size}|{chunk_overlap}".encode()).hexdigest()
    model = slugify(embedding_model.rsplit("/", 1)[-1], 24)
    return f"{COLLECTION_PREFIX}{model}_{chunk_size}-{chunk_overlap}_{digest[:8]}"


@dataclass(frozen=True, slots=True)
class ChunkPoint:
    id: uuid.UUID
    dense: list[float]
    sparse: SparseVector
    payload: dict[str, Any]


def _document_filter(
    document_id: uuid.UUID, *, index_version: int | None = None, exclude_version: int | None = None
) -> qm.Filter:
    must: list[qm.Condition] = [qm.FieldCondition(key="document_id", match=qm.MatchValue(value=str(document_id)))]
    must_not: list[qm.Condition] = []
    if index_version is not None:
        must.append(qm.FieldCondition(key="index_version", match=qm.MatchValue(value=index_version)))
    if exclude_version is not None:
        must_not.append(qm.FieldCondition(key="index_version", match=qm.MatchValue(value=exclude_version)))
    return qm.Filter(must=must, must_not=must_not or None)


class QdrantVectorStore:
    def __init__(self, client: AsyncQdrantClient, alias: str) -> None:
        self.client = client
        self.alias = alias

    # --- collections -----------------------------------------------------------------------
    async def ensure_collection(self, name: str, dim: int) -> None:
        if not await self.client.collection_exists(name):
            log.info("creating_qdrant_collection", collection=name, dim=dim)
            await self.client.create_collection(
                collection_name=name,
                vectors_config={DENSE: qm.VectorParams(size=dim, distance=qm.Distance.COSINE, on_disk=True)},
                sparse_vectors_config={SPARSE: qm.SparseVectorParams(modifier=qm.Modifier.IDF)},
                # Multitenancy: build the HNSW graph per tenant (org_id) instead of globally.
                hnsw_config=qm.HnswConfigDiff(m=0, payload_m=16),
                quantization_config=qm.ScalarQuantization(
                    scalar=qm.ScalarQuantizationConfig(type=qm.ScalarType.INT8, always_ram=True)
                ),
            )
        await self.client.create_payload_index(
            name, "org_id", field_schema=qm.KeywordIndexParams(type=qm.KeywordIndexType.KEYWORD, is_tenant=True)
        )
        for field in _KEYWORD_FIELDS:
            await self.client.create_payload_index(name, field, field_schema=qm.PayloadSchemaType.KEYWORD)
        for field in _INTEGER_FIELDS:
            await self.client.create_payload_index(name, field, field_schema=qm.PayloadSchemaType.INTEGER)
        await self.client.create_payload_index(name, "searchable", field_schema=qm.PayloadSchemaType.BOOL)

    async def activate(self, name: str) -> None:
        """Point the retrieval alias at ``name`` (atomic switch)."""
        aliases = (await self.client.get_aliases()).aliases
        current = next((a.collection_name for a in aliases if a.alias_name == self.alias), None)
        if current == name:
            return
        ops: list[Any] = []
        if current is not None:
            ops.append(qm.DeleteAliasOperation(delete_alias=qm.DeleteAlias(alias_name=self.alias)))
        ops.append(qm.CreateAliasOperation(create_alias=qm.CreateAlias(collection_name=name, alias_name=self.alias)))
        await self.client.update_collection_aliases(change_aliases_operations=ops)
        log.info("qdrant_alias_switched", alias=self.alias, collection=name, previous=current)

    async def documind_collections(self) -> list[str]:
        response = await self.client.get_collections()
        return [c.name for c in response.collections if c.name.startswith(COLLECTION_PREFIX)]

    # --- points ----------------------------------------------------------------------------
    async def upsert(self, collection: str, points: Sequence[ChunkPoint]) -> None:
        for i in range(0, len(points), _UPSERT_BATCH):
            batch = points[i : i + _UPSERT_BATCH]
            await self.client.upsert(
                collection_name=collection,
                points=[
                    qm.PointStruct(
                        id=str(p.id),
                        vector={
                            DENSE: p.dense,
                            SPARSE: qm.SparseVector(indices=p.sparse.indices, values=p.sparse.values),
                        },
                        payload=p.payload,
                    )
                    for p in batch
                ],
                wait=True,
            )

    async def set_document_payload(
        self, collection: str, document_id: uuid.UUID, payload: dict[str, Any], *, index_version: int | None = None
    ) -> None:
        await self.client.set_payload(
            collection_name=collection,
            payload=payload,
            points=qm.FilterSelector(filter=_document_filter(document_id, index_version=index_version)),
            wait=True,
        )

    async def delete_document(
        self,
        collection: str,
        document_id: uuid.UUID,
        *,
        index_version: int | None = None,
        exclude_version: int | None = None,
    ) -> None:
        if not await self.client.collection_exists(collection):
            return
        await self.client.delete(
            collection_name=collection,
            points_selector=qm.FilterSelector(
                filter=_document_filter(document_id, index_version=index_version, exclude_version=exclude_version)
            ),
            wait=True,
        )

    async def delete_document_everywhere(self, document_id: uuid.UUID) -> None:
        """Remove a document's points from every DocuMind collection (all embedding configs)."""
        for name in await self.documind_collections():
            await self.delete_document(name, document_id)

    async def count(
        self,
        collection: str,
        document_id: uuid.UUID,
        *,
        index_version: int | None = None,
        searchable: bool | None = None,
    ) -> int:
        flt = _document_filter(document_id, index_version=index_version)
        if searchable is not None:
            flt.must.append(qm.FieldCondition(key="searchable", match=qm.MatchValue(value=searchable)))  # type: ignore[union-attr]
        return (await self.client.count(collection, count_filter=flt, exact=True)).count
