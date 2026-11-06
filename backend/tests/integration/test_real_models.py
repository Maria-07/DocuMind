"""Ingestion with the real production components: bge-base-en-v1.5 (ONNX), BM25, HF tokenizer.

Opt-in (downloads ~220 MB of models on first run):  RUN_MODEL_TESTS=1 TEST_QDRANT_URL=... pytest ...
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qm

from app.core.permissions import Principal, permissions_for
from app.core.resources import AppResources
from app.models.enums import Role
from app.rag.ingestion.pipeline import IngestionPipeline
from app.rag.setup import build_ingestion_pipeline
from app.rag.vectorstore import DENSE, SPARSE
from app.services.access_policy import principal_acl_tokens
from tests.documents import build_docx, build_xlsx
from tests.integration.api_helpers import register, upload_one
from tests.integration.test_ingestion_pipeline import _latest_job

TEST_QDRANT_URL = os.environ.get("TEST_QDRANT_URL")
MODEL_CACHE = Path(__file__).resolve().parents[2] / ".models"
pytestmark = pytest.mark.skipif(
    not (TEST_QDRANT_URL and os.environ.get("RUN_MODEL_TESTS")), reason="RUN_MODEL_TESTS / TEST_QDRANT_URL not set"
)


@dataclass
class RealPipeline:
    pipeline: IngestionPipeline
    org_ids: list[str] = field(default_factory=list)  # tenants created by the test, cleaned up after


@pytest.fixture
async def real(resources: AppResources) -> AsyncIterator[RealPipeline]:
    resources.settings.model_cache_dir = MODEL_CACHE
    resources.settings.qdrant_collection_alias = f"test_alias_{uuid.uuid4().hex[:8]}"
    client = AsyncQdrantClient(url=TEST_QDRANT_URL)
    holder = RealPipeline(
        await build_ingestion_pipeline(
            settings=resources.settings, sessionmaker=resources.sessionmaker, storage=resources.storage, qdrant=client
        )
    )
    yield holder
    p = holder.pipeline
    await client.update_collection_aliases(
        change_aliases_operations=[qm.DeleteAliasOperation(delete_alias=qm.DeleteAlias(alias_name=p.store.alias))]
    )
    # The collection belongs to the real default config: remove only this test's tenants.
    for org_id in holder.org_ids:
        await client.delete(
            p.collection,
            points_selector=qm.FilterSelector(
                filter=qm.Filter(must=[qm.FieldCondition(key="org_id", match=qm.MatchValue(value=org_id))])
            ),
            wait=True,
        )
    await client.close()


async def _hybrid_top(pipeline: IngestionPipeline, principal: Principal, question: str) -> qm.ScoredPoint:
    flt = qm.Filter(
        must=[
            qm.FieldCondition(key="org_id", match=qm.MatchValue(value=str(principal.organization_id))),
            qm.FieldCondition(key="searchable", match=qm.MatchValue(value=True)),
            qm.FieldCondition(key="acl", match=qm.MatchAny(any=principal_acl_tokens(principal))),
        ]
    )
    dense = await pipeline.embedder.embed_query(question)
    sparse = await pipeline.sparse.encode_query(question)
    result = await pipeline.store.client.query_points(
        pipeline.collection,
        prefetch=[
            qm.Prefetch(query=dense, using=DENSE, limit=20, filter=flt),
            qm.Prefetch(
                query=qm.SparseVector(indices=sparse.indices, values=sparse.values), using=SPARSE, limit=20, filter=flt
            ),
        ],
        query=qm.FusionQuery(fusion=qm.Fusion.RRF),
        limit=3,
        with_payload=True,
    )
    return result.points[0]


async def test_real_models_index_and_retrieve(
    client: httpx.AsyncClient, real: RealPipeline, resources: AppResources
) -> None:
    pipeline = real.pipeline
    assert await pipeline.embedder.dimension() == 768
    admin = await register(client)
    real.org_ids.append(admin.org_id)
    policy = await upload_one(admin, "Security Policy.docx", build_docx())
    revenue = await upload_one(admin, "Revenue 2026.xlsx", build_xlsx(rows=12))
    for doc in (policy, revenue):
        job = await _latest_job(resources, doc["id"])
        assert await pipeline.run(uuid.UUID(doc["id"]), job.id, attempt=1) == "indexed"

    principal = Principal(
        user_id=uuid.UUID(admin.user_id),
        organization_id=uuid.UUID(admin.org_id),
        role=Role.ADMIN,
        email="a@example.com",
        permissions=permissions_for(Role.ADMIN),
    )
    top = await _hybrid_top(pipeline, principal, "How often do passwords have to be changed?")
    assert top.payload["section_path"] == "Security Policy > Access Control > Passwords"
    assert "90 days" in top.payload["text"]

    top = await _hybrid_top(pipeline, principal, "How long do we keep customer records?")
    assert "seven years" in top.payload["text"]

    top = await _hybrid_top(pipeline, principal, "APAC revenue figures by quarter")
    assert top.payload["sheet_name"] == "Revenue" and "APAC" in top.payload["text"]

    # Chunk sizes are measured with the real tokenizer and respect the configured limit.
    resp = await client.get(f"/api/v1/documents/{revenue['id']}/chunks", headers=admin.headers)
    assert all(c["token_count"] <= resources.settings.chunk_size_tokens for c in resp.json()["items"])
