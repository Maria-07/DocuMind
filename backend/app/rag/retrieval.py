"""Permission-aware hybrid retrieval.

    query → embed (dense + BM25) → Qdrant (filter: org ∧ searchable ∧ ACL ∧ scope; dense ⊕ sparse via RRF)
          → PostgreSQL post-check (live document, can_view, active index version)
          → cross-encoder rerank → relevance threshold → per-document diversity cap → token budget

Authorization happens twice: inside the vector search (the filter is built only from the
server-side Principal) and again against PostgreSQL, the source of truth — so a stale Qdrant
payload can only ever make results *narrower*.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Literal

import structlog
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.permissions import Principal
from app.models import Document, DocumentPermission
from app.rag.embeddings.base import EmbeddingProvider, SparseEncoder
from app.rag.reranking import Reranker
from app.rag.vectorstore import DENSE, SPARSE
from app.services.access_policy import can_view, principal_acl_tokens

log = structlog.get_logger(__name__)

SearchMode = Literal["hybrid", "semantic"]


@dataclass(frozen=True, slots=True)
class RetrievalFilters:
    document_ids: tuple[uuid.UUID, ...] = ()
    category_ids: tuple[uuid.UUID, ...] = ()
    file_types: tuple[str, ...] = ()

    @classmethod
    def from_scope(cls, scope: dict[str, Any] | None) -> RetrievalFilters:
        scope = scope or {}
        return cls(
            document_ids=tuple(uuid.UUID(str(x)) for x in scope.get("document_ids") or []),
            category_ids=tuple(uuid.UUID(str(x)) for x in scope.get("category_ids") or []),
            file_types=tuple(str(x) for x in scope.get("file_types") or []),
        )


@dataclass(slots=True)
class RetrievedChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    file_type: str
    kind: str
    text: str
    token_count: int
    index_version: int
    page_start: int | None = None
    page_end: int | None = None
    sheet_name: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    section_path: str | None = None
    flags: list[str] = field(default_factory=list)
    fused_score: float = 0.0
    relevance: float | None = None  # reranker score in [0, 1]

    @property
    def location(self) -> str:
        parts: list[str] = []
        if self.page_start is not None:
            parts.append(
                f"p. {self.page_start}"
                if self.page_end in (None, self.page_start)
                else f"pp. {self.page_start}-{self.page_end}"
            )
        if self.sheet_name:
            rows = f", rows {self.row_start}-{self.row_end}" if self.row_start is not None else ""
            parts.append(f"sheet {self.sheet_name}{rows}")
        if self.section_path:
            parts.append(self.section_path)
        return " · ".join(parts)


@dataclass(slots=True)
class RetrievalResult:
    chunks: list[RetrievedChunk]
    mode: SearchMode
    candidate_count: int
    filtered_out: int
    timings_ms: dict[str, float]

    @property
    def top_relevance(self) -> float | None:
        scores = [c.relevance for c in self.chunks if c.relevance is not None]
        return max(scores) if scores else None


def build_filter(principal: Principal, filters: RetrievalFilters | None = None) -> qm.Filter:
    """The Qdrant filter for ``principal`` — derived only from server-side state."""
    must: list[qm.Condition] = [
        qm.FieldCondition(key="org_id", match=qm.MatchValue(value=str(principal.organization_id))),
        qm.FieldCondition(key="searchable", match=qm.MatchValue(value=True)),
        qm.FieldCondition(key="acl", match=qm.MatchAny(any=principal_acl_tokens(principal))),
    ]
    if filters is not None:
        if filters.document_ids:
            must.append(
                qm.FieldCondition(key="document_id", match=qm.MatchAny(any=[str(d) for d in filters.document_ids]))
            )
        if filters.category_ids:
            must.append(
                qm.FieldCondition(key="category_id", match=qm.MatchAny(any=[str(c) for c in filters.category_ids]))
            )
        if filters.file_types:
            must.append(qm.FieldCondition(key="file_type", match=qm.MatchAny(any=list(filters.file_types))))
    return qm.Filter(must=must)


def _chunk_from_point(point: qm.ScoredPoint) -> RetrievedChunk:
    p = point.payload or {}
    return RetrievedChunk(
        chunk_id=uuid.UUID(str(point.id)),
        document_id=uuid.UUID(p["document_id"]),
        document_title=p.get("document_title") or "Untitled",
        file_type=p.get("file_type") or "",
        kind=p.get("kind") or "text",
        text=p.get("text") or "",
        token_count=int(p.get("token_count") or 0),
        index_version=int(p.get("index_version") or 0),
        page_start=p.get("page_start"),
        page_end=p.get("page_end"),
        sheet_name=p.get("sheet_name"),
        row_start=p.get("row_start"),
        row_end=p.get("row_end"),
        section_path=p.get("section_path"),
        flags=list(p.get("flags") or []),
        fused_score=float(point.score or 0.0),
    )


def select_chunks(
    chunks: list[RetrievedChunk],
    *,
    top_n: int,
    min_relevance: float | None,
    per_document_cap: int | None,
    token_budget: int | None,
) -> list[RetrievedChunk]:
    """Order by relevance (reranked) or fused score, then apply threshold, diversity cap and budget."""
    ordered = sorted(
        chunks, key=lambda c: (c.relevance if c.relevance is not None else -1.0, c.fused_score), reverse=True
    )
    selected: list[RetrievedChunk] = []
    per_doc: dict[uuid.UUID, int] = defaultdict(int)
    tokens = 0
    for c in ordered:
        if min_relevance is not None and c.relevance is not None and c.relevance < min_relevance:
            continue
        if per_document_cap is not None and per_doc[c.document_id] >= per_document_cap:
            continue
        if token_budget is not None and selected and tokens + c.token_count > token_budget:
            continue
        selected.append(c)
        per_doc[c.document_id] += 1
        tokens += c.token_count
        if len(selected) >= top_n:
            break
    return selected


class Retriever:
    def __init__(
        self,
        *,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        client: AsyncQdrantClient,
        collection: str,
        embedder: EmbeddingProvider,
        sparse: SparseEncoder,
        reranker: Reranker | None,
    ) -> None:
        self.settings = settings
        self.sessionmaker = sessionmaker
        self.client = client
        self.collection = collection
        self.embedder = embedder
        self.sparse = sparse
        self.reranker = reranker

    async def retrieve(
        self,
        principal: Principal,
        query: str,
        *,
        mode: SearchMode = "hybrid",
        filters: RetrievalFilters | None = None,
        top_n: int | None = None,
        rerank: bool = True,
        apply_threshold: bool = True,
        diversify: bool = True,
        token_budget: int | None = None,
    ) -> RetrievalResult:
        s = self.settings
        top_n = top_n or s.retrieval_top_n
        min_relevance: float | None = s.retrieval_min_relevance if apply_threshold else None
        # A scope of exactly one document may use every slot.
        single_document = filters is not None and len(filters.document_ids) == 1
        per_document_cap = s.retrieval_max_chunks_per_document if diversify and not single_document else None
        timings: dict[str, float] = {}
        flt = build_filter(principal, filters)
        k = s.retrieval_candidates

        t = time.perf_counter()
        if mode == "hybrid":
            dense, sparse = await asyncio.gather(self.embedder.embed_query(query), self.sparse.encode_query(query))
        else:
            dense, sparse = await self.embedder.embed_query(query), None
        timings["embed_ms"] = (time.perf_counter() - t) * 1000

        t = time.perf_counter()
        if sparse is not None:
            response = await self.client.query_points(
                self.collection,
                prefetch=[
                    qm.Prefetch(query=dense, using=DENSE, limit=k, filter=flt),
                    qm.Prefetch(
                        query=qm.SparseVector(indices=sparse.indices, values=sparse.values),
                        using=SPARSE,
                        limit=k,
                        filter=flt,
                    ),
                ],
                query=qm.FusionQuery(fusion=qm.Fusion.RRF),
                limit=k,
                with_payload=True,
            )
        else:
            response = await self.client.query_points(
                self.collection, query=dense, using=DENSE, query_filter=flt, limit=k, with_payload=True
            )
        candidates = [_chunk_from_point(p) for p in response.points]
        timings["search_ms"] = (time.perf_counter() - t) * 1000

        t = time.perf_counter()
        allowed = await self._post_check(principal, candidates)
        filtered_out = len(candidates) - len(allowed)
        if filtered_out:
            log.info("retrieval_postcheck_removed", removed=filtered_out)
        timings["postcheck_ms"] = (time.perf_counter() - t) * 1000

        if rerank and self.reranker is not None and allowed:
            # Cross-encoders are expensive: rerank only the best fused candidates.
            allowed = allowed[: s.rerank_candidates]
            t = time.perf_counter()
            passages = [f"{c.document_title}\n{c.location}\n{c.text}" for c in allowed]
            for c, score in zip(allowed, await self.reranker.score(query, passages), strict=True):
                c.relevance = score
            timings["rerank_ms"] = (time.perf_counter() - t) * 1000
        else:
            min_relevance = None  # scores are only comparable to the threshold after reranking

        chunks = select_chunks(
            allowed,
            top_n=top_n,
            min_relevance=min_relevance,
            per_document_cap=per_document_cap,
            token_budget=token_budget,
        )
        return RetrievalResult(
            chunks=chunks,
            mode=mode,
            candidate_count=len(candidates),
            filtered_out=filtered_out,
            timings_ms={k: round(v, 1) for k, v in timings.items()},
        )

    async def _post_check(self, principal: Principal, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]:
        if not candidates:
            return []
        doc_ids = {c.document_id for c in candidates}
        async with self.sessionmaker() as s:
            documents = (
                (
                    await s.execute(
                        select(Document).where(
                            Document.id.in_(doc_ids),
                            Document.organization_id == principal.organization_id,
                            Document.deleted_at.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            grants: dict[uuid.UUID, list[DocumentPermission]] = defaultdict(list)
            for g in (
                await s.execute(select(DocumentPermission).where(DocumentPermission.document_id.in_(doc_ids)))
            ).scalars():
                grants[g.document_id].append(g)
        visible = {d.id: d for d in documents if can_view(principal, d, grants[d.id])}
        return [
            c
            for c in candidates
            if c.document_id in visible and c.index_version == visible[c.document_id].active_index_version
        ]
