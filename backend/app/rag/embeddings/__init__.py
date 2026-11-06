from __future__ import annotations

from app.core.config import Settings
from app.rag.embeddings.base import EmbeddingProvider, SparseEncoder, SparseVector
from app.rag.embeddings.fastembed_provider import FastEmbedBM25Encoder, FastEmbedProvider


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "fastembed":
        return FastEmbedProvider(
            settings.embedding_model, cache_dir=settings.model_cache_dir, batch_size=settings.embedding_batch_size
        )
    raise ValueError(f"Unknown embedding provider: {settings.embedding_provider}")


def build_sparse_encoder(settings: Settings) -> SparseEncoder:
    return FastEmbedBM25Encoder(settings.sparse_model, cache_dir=settings.model_cache_dir)


__all__ = [
    "EmbeddingProvider",
    "SparseEncoder",
    "SparseVector",
    "build_embedding_provider",
    "build_sparse_encoder",
]
