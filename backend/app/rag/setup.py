"""Bootstrap of the active embedding configuration and the ingestion pipeline."""

from __future__ import annotations

import structlog
from qdrant_client import AsyncQdrantClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models import EmbeddingConfig
from app.rag.chunking.chunker import Chunker
from app.rag.chunking.tokens import HuggingFaceTokenCounter, TokenCounter
from app.rag.embeddings import EmbeddingProvider, SparseEncoder, build_embedding_provider, build_sparse_encoder
from app.rag.ingestion.pipeline import IngestionPipeline
from app.rag.vectorstore import QdrantVectorStore, collection_name
from app.services.storage import LocalStorage

log = structlog.get_logger(__name__)


async def ensure_active_embedding_config(
    sessionmaker: async_sessionmaker[AsyncSession], settings: Settings, dim: int
) -> EmbeddingConfig:
    """Upsert the embedding_configs row for the current settings and make it the only active one."""
    name = collection_name(
        settings.embedding_model, settings.sparse_model, settings.chunk_size_tokens, settings.chunk_overlap_tokens
    )
    async with sessionmaker() as s:
        config = (await s.execute(select(EmbeddingConfig).where(EmbeddingConfig.name == name))).scalar_one_or_none()
        if config is None:
            config = EmbeddingConfig(
                name=name,
                embedding_provider=settings.embedding_provider,
                embedding_model=settings.embedding_model,
                dim=dim,
                sparse_model=settings.sparse_model,
                chunk_size=settings.chunk_size_tokens,
                chunk_overlap=settings.chunk_overlap_tokens,
                qdrant_collection=name,
                is_active=False,
            )
            s.add(config)
            await s.flush()
        elif config.dim != dim:
            raise RuntimeError(f"Embedding config {name} expects dim {config.dim}, model produced {dim}")
        if not config.is_active:
            await s.execute(update(EmbeddingConfig).where(EmbeddingConfig.id != config.id).values(is_active=False))
            await s.flush()
            config.is_active = True
            log.info("embedding_config_activated", config=name)
        await s.commit()
        return config


async def build_ingestion_pipeline(
    *,
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    qdrant: AsyncQdrantClient,
    embedder: EmbeddingProvider | None = None,
    sparse: SparseEncoder | None = None,
    token_counter: TokenCounter | None = None,
) -> IngestionPipeline:
    embedder = embedder or build_embedding_provider(settings)
    sparse = sparse or build_sparse_encoder(settings)
    counter = token_counter or HuggingFaceTokenCounter(settings.tokenizer_name, settings.model_cache_dir)
    dim = await embedder.dimension()  # loads (and on first run downloads) the model: fail fast

    config = await ensure_active_embedding_config(sessionmaker, settings, dim)
    store = QdrantVectorStore(qdrant, settings.qdrant_collection_alias)
    await store.ensure_collection(config.qdrant_collection, dim)
    await store.activate(config.qdrant_collection)

    return IngestionPipeline(
        settings=settings,
        sessionmaker=sessionmaker,
        storage=storage,
        store=store,
        embedder=embedder,
        sparse=sparse,
        chunker=Chunker(counter, chunk_size=settings.chunk_size_tokens, chunk_overlap=settings.chunk_overlap_tokens),
        embedding_config=config,
    )
