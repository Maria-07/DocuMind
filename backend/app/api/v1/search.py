from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from app.api.deps import CurrentPrincipal, LinkServiceDep, SearchServiceDep, rate_limit_by_user
from app.rag.retrieval import RetrievalFilters
from app.schemas.common import ERROR_RESPONSES
from app.schemas.links import LinkSearchRequest, LinkSearchResponse
from app.schemas.search import DocumentSearchResponse, SearchRequest, SearchResponse
from app.services.links import to_link_read
from app.services.search import to_hit

router = APIRouter(
    prefix="/search",
    tags=["search"],
    responses=ERROR_RESPONSES,
    dependencies=[Depends(rate_limit_by_user("search", "rate_limit_search_per_minute"))],
)


@router.post("/semantic", response_model=SearchResponse, summary="Dense vector search (+ optional reranking)")
async def semantic_search(
    data: SearchRequest, principal: CurrentPrincipal, service: SearchServiceDep
) -> SearchResponse:
    result, took = await service.search_chunks(principal, data, "semantic")
    return SearchResponse(
        mode="semantic",
        results=[to_hit(c) for c in result.chunks],
        candidate_count=result.candidate_count,
        took_ms=round(took, 1),
    )


@router.post("/hybrid", response_model=SearchResponse, summary="Dense + BM25 fused with RRF (+ optional reranking)")
async def hybrid_search(data: SearchRequest, principal: CurrentPrincipal, service: SearchServiceDep) -> SearchResponse:
    result, took = await service.search_chunks(principal, data, "hybrid")
    return SearchResponse(
        mode="hybrid",
        results=[to_hit(c) for c in result.chunks],
        candidate_count=result.candidate_count,
        took_ms=round(took, 1),
    )


@router.post("/documents", response_model=DocumentSearchResponse, summary="Which documents mention …?")
async def document_search(
    data: SearchRequest, principal: CurrentPrincipal, service: SearchServiceDep
) -> DocumentSearchResponse:
    hits, took = await service.search_documents(principal, data)
    return DocumentSearchResponse(results=hits, took_ms=round(took, 1))


@router.post(
    "/links",
    response_model=LinkSearchResponse,
    summary="Find hyperlinks by platform, domain, anchor text or keyword (exact matching, no embeddings)",
)
async def link_search(
    data: LinkSearchRequest, principal: CurrentPrincipal, links: LinkServiceDep
) -> LinkSearchResponse:
    started = time.perf_counter()
    filters = RetrievalFilters.from_scope(data.filters.model_dump(mode="json") if data.filters else None)
    hits = await links.search(principal, data.query, filters=filters, limit=data.limit)
    return LinkSearchResponse(
        results=[to_link_read(h) for h in hits], took_ms=round((time.perf_counter() - started) * 1000, 1)
    )
