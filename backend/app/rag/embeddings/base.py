from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SparseVector:
    indices: list[int]
    values: list[float]


class EmbeddingProvider(Protocol):
    """Dense embeddings. Implementations must be safe to call from the event loop (offload CPU work)."""

    @property
    def model_id(self) -> str: ...

    async def dimension(self) -> int: ...

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class SparseEncoder(Protocol):
    """Sparse lexical vectors (BM25) for the keyword half of hybrid search."""

    @property
    def model_id(self) -> str: ...

    async def encode_documents(self, texts: list[str]) -> list[SparseVector]: ...

    async def encode_query(self, text: str) -> SparseVector: ...
