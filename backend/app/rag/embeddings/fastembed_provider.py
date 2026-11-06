"""Local ONNX embeddings via fastembed (no API key; models are downloaded once into MODEL_CACHE_DIR)."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import anyio
import structlog

from app.rag.embeddings.base import SparseVector

log = structlog.get_logger(__name__)


class _LazyModel:
    """Load the model on first use (first call may download it), once, thread-safely."""

    def __init__(self, factory: Any, model_name: str, cache_dir: Path, threads: int | None) -> None:
        self._factory = factory
        self._model_name = model_name
        self._cache_dir = cache_dir
        self._threads = threads
        self._model: Any = None
        self._lock = threading.Lock()

    def get(self) -> Any:
        if self._model is None:
            with self._lock:
                if self._model is None:
                    log.info("loading_embedding_model", model=self._model_name)
                    self._cache_dir.mkdir(parents=True, exist_ok=True)
                    self._model = self._factory(
                        model_name=self._model_name, cache_dir=str(self._cache_dir), threads=self._threads
                    )
        return self._model


class FastEmbedProvider:
    def __init__(self, model_name: str, *, cache_dir: Path, batch_size: int = 32, threads: int | None = None) -> None:
        from fastembed import TextEmbedding

        self._name = model_name
        self._batch_size = batch_size
        self._model = _LazyModel(TextEmbedding, model_name, cache_dir, threads)
        self._dim: int | None = None

    @property
    def model_id(self) -> str:
        return self._name

    async def dimension(self) -> int:
        if self._dim is None:
            self._dim = len(await self.embed_query("dimension probe"))
        return self._dim

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        def run() -> list[list[float]]:
            model = self._model.get()
            return [v.tolist() for v in model.embed(texts, batch_size=self._batch_size)]

        return await anyio.to_thread.run_sync(run)

    async def embed_query(self, text: str) -> list[float]:
        def run() -> list[float]:
            return next(iter(self._model.get().query_embed(text))).tolist()  # type: ignore[no-any-return]

        return await anyio.to_thread.run_sync(run)


class FastEmbedBM25Encoder:
    def __init__(self, model_name: str = "Qdrant/bm25", *, cache_dir: Path, batch_size: int = 64) -> None:
        from fastembed import SparseTextEmbedding

        self._name = model_name
        self._batch_size = batch_size
        self._model = _LazyModel(SparseTextEmbedding, model_name, cache_dir, None)

    @property
    def model_id(self) -> str:
        return self._name

    async def encode_documents(self, texts: list[str]) -> list[SparseVector]:
        if not texts:
            return []

        def run() -> list[SparseVector]:
            return [
                SparseVector(indices=e.indices.tolist(), values=e.values.tolist())
                for e in self._model.get().embed(texts, batch_size=self._batch_size)
            ]

        return await anyio.to_thread.run_sync(run)

    async def encode_query(self, text: str) -> SparseVector:
        def run() -> SparseVector:
            e = next(iter(self._model.get().query_embed(text)))
            return SparseVector(indices=e.indices.tolist(), values=e.values.tolist())

        return await anyio.to_thread.run_sync(run)
