"""RAG components used by the API process (models load lazily on first use)."""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.rag.embeddings import EmbeddingProvider, SparseEncoder, build_embedding_provider, build_sparse_encoder
from app.rag.generation import build_llm
from app.rag.generation.base import LLMProvider
from app.rag.reranking import FastEmbedReranker, Reranker
from app.rag.retrieval import Retriever

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class RagRuntime:
    retriever: Retriever
    llm: LLMProvider | None  # None when no LLM credentials are configured (search still works)


def build_rag_runtime(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    qdrant: AsyncQdrantClient,
    *,
    embedder: EmbeddingProvider | None = None,
    sparse: SparseEncoder | None = None,
    reranker: Reranker | None = None,
    llm: LLMProvider | None = None,
) -> RagRuntime:
    if reranker is None and settings.reranker_enabled:
        reranker = FastEmbedReranker(settings.reranker_model, cache_dir=settings.model_cache_dir)
    if llm is None:
        llm = build_llm(settings)
    if llm is None:
        key = "OPENAI_API_KEY" if settings.llm_provider == "openai" else "ANTHROPIC_API_KEY"
        log.warning("llm_not_configured", provider=settings.llm_provider, hint=f"set {key} to enable chat answers")
    else:
        log.info("llm_configured", provider=settings.llm_provider, model=settings.llm_model)
    retriever = Retriever(
        settings=settings,
        sessionmaker=sessionmaker,
        client=qdrant,
        collection=settings.qdrant_collection_alias,  # the alias always points at the active config
        embedder=embedder or build_embedding_provider(settings),
        sparse=sparse or build_sparse_encoder(settings),
        reranker=reranker,
    )
    return RagRuntime(retriever=retriever, llm=llm)
