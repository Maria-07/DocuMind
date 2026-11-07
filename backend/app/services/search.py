from __future__ import annotations

import time
import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.permissions import Principal
from app.models import EmbeddingConfig, RetrievalLog
from app.rag.retrieval import RetrievalFilters, RetrievalResult, RetrievedChunk, Retriever, SearchMode
from app.schemas.search import DocumentSearchHit, SearchHit, SearchRequest


def filters_from(request_scope: object | None) -> RetrievalFilters | None:
    if request_scope is None:
        return None
    return RetrievalFilters.from_scope(request_scope.as_dict())  # type: ignore[attr-defined]


def to_hit(c: RetrievedChunk) -> SearchHit:
    return SearchHit(
        chunk_id=c.chunk_id,
        document_id=c.document_id,
        document_title=c.document_title,
        file_type=c.file_type,
        kind=c.kind,
        text=c.text,
        location=c.location,
        page_start=c.page_start,
        page_end=c.page_end,
        sheet_name=c.sheet_name,
        row_start=c.row_start,
        row_end=c.row_end,
        section_path=c.section_path,
        score=c.fused_score,
        relevance=c.relevance,
    )


async def log_retrieval(
    sessionmaker: async_sessionmaker[AsyncSession],
    principal: Principal,
    result: RetrievalResult,
    *,
    source: str,
    message_id: uuid.UUID | None = None,
) -> None:
    async with sessionmaker() as s:
        config_id = await s.scalar(select(EmbeddingConfig.id).where(EmbeddingConfig.is_active.is_(True)))
        s.add(
            RetrievalLog(
                organization_id=principal.organization_id,
                user_id=principal.user_id,
                message_id=message_id,
                source=source,
                mode=result.mode,
                candidate_count=result.candidate_count,
                filtered_out=result.filtered_out,
                returned_chunk_ids=[c.chunk_id for c in result.chunks],
                top_score=result.top_relevance,
                no_answer=not result.chunks,
                latency=result.timings_ms,
                embedding_config_id=config_id,
            )
        )
        await s.commit()


class SearchService:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], retriever: Retriever) -> None:
        self.sessionmaker = sessionmaker
        self.retriever = retriever

    async def search_chunks(
        self, principal: Principal, request: SearchRequest, mode: SearchMode
    ) -> tuple[RetrievalResult, float]:
        started = time.perf_counter()
        result = await self.retriever.retrieve(
            principal,
            request.query,
            mode=mode,
            filters=filters_from(request.filters),
            top_n=request.top_k,
            rerank=request.rerank,
            # Search shows ranked results; the "no answer" threshold is a chat concern.
            apply_threshold=False,
            diversify=False,
        )
        await log_retrieval(self.sessionmaker, principal, result, source="search")
        return result, (time.perf_counter() - started) * 1000

    async def search_documents(
        self, principal: Principal, request: SearchRequest
    ) -> tuple[list[DocumentSearchHit], float]:
        """ "Which documents mention X?" — chunk hits grouped per document, best match first."""
        started = time.perf_counter()
        result = await self.retriever.retrieve(
            principal,
            request.query,
            mode="hybrid",
            filters=filters_from(request.filters),
            top_n=50,
            rerank=request.rerank,
            apply_threshold=True,
            diversify=False,
        )
        await log_retrieval(self.sessionmaker, principal, result, source="search")
        groups: dict[uuid.UUID, list[RetrievedChunk]] = defaultdict(list)
        for c in result.chunks:  # already ordered best first
            groups[c.document_id].append(c)
        hits = [
            DocumentSearchHit(
                document_id=doc_id,
                document_title=chunks[0].document_title,
                file_type=chunks[0].file_type,
                best_relevance=chunks[0].relevance,
                matching_chunks=len(chunks),
                snippet=chunks[0].text[:400],
                location=chunks[0].location,
            )
            for doc_id, chunks in groups.items()
        ][: request.top_k]
        return hits, (time.perf_counter() - started) * 1000
