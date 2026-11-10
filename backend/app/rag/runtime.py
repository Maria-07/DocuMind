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
from app.rag.generation.registry import ModelRegistry
from app.rag.reranking import FastEmbedReranker, Reranker
from app.rag.retrieval import Retriever

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class RagRuntime:
    retriever: Retriever
    llm: LLMProvider | None  # the default model; None when its credentials are missing (search still works)
    models: ModelRegistry  # every model users can pick in chat (default included), with prices


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
    models = ModelRegistry(settings, default=llm)
    available = [o.spec.id for o in models.available()]
    if not available:
        log.warning("llm_not_configured", provider=settings.llm_provider, hint="set an LLM API key to enable chat")
    else:
        log.info("llm_configured", default=models.default_id, available=available)
    retriever = Retriever(
        settings=settings,
        sessionmaker=sessionmaker,
        client=qdrant,
        collection=settings.qdrant_collection_alias,  # the alias always points at the active config
        embedder=embedder or build_embedding_provider(settings),
        sparse=sparse or build_sparse_encoder(settings),
        reranker=reranker,
    )
    return RagRuntime(retriever=retriever, llm=llm, models=models)
