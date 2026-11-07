"""Cross-encoder reranking: scores (query, passage) pairs jointly, far more precise than vector similarity."""

from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Any, Protocol

import anyio
import structlog

log = structlog.get_logger(__name__)


class Reranker(Protocol):
    @property
    def model_id(self) -> str: ...

    async def score(self, query: str, passages: list[str]) -> list[float]:
        """Relevance in [0, 1] per passage (same order)."""
        ...


def sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    z = math.exp(x)
    return z / (1.0 + z)


class FastEmbedReranker:
    def __init__(self, model_name: str, *, cache_dir: Path, batch_size: int = 32) -> None:
        self._name = model_name
        self._cache_dir = cache_dir
        self._batch_size = batch_size
        self._model: Any = None
        self._lock = threading.Lock()

    @property
    def model_id(self) -> str:
        return self._name

    def _get(self) -> Any:
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from fastembed.rerank.cross_encoder import TextCrossEncoder

                    log.info("loading_reranker", model=self._name)
                    self._cache_dir.mkdir(parents=True, exist_ok=True)
                    self._model = TextCrossEncoder(model_name=self._name, cache_dir=str(self._cache_dir))
        return self._model

    async def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []

        def run() -> list[float]:
            logits = self._get().rerank(query, passages, batch_size=self._batch_size)
            return [sigmoid(float(x)) for x in logits]

        return await anyio.to_thread.run_sync(run)
