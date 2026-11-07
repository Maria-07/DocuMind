"""Deterministic test doubles implementing the real provider protocols (no network, no models).

They exercise all pipeline/retrieval/chat logic exactly; real models and the real LLM are covered
by the opt-in tests (test_real_models.py, test_real_answers.py).
"""

from __future__ import annotations

import zlib
from collections.abc import AsyncIterator, Callable
from typing import Any

from app.rag.embeddings.base import SparseVector
from app.rag.generation.base import (
    BlockEnd,
    ChatTurn,
    CitationDelta,
    GenerationDone,
    GenerationError,
    GenerationEvent,
    SourceDocument,
    TextDelta,
)

DIM = 64


def tokens(text: str) -> list[str]:
    return [w.strip(".,:;|()?!\"'").lower() for w in text.split() if w.strip(".,:;|()?!\"'")]


class HashEmbedder:
    """Bag-of-words hashing embedder (lexical, deterministic)."""

    def __init__(self) -> None:
        self.calls = 0
        self.before_call: Callable[[], Any] | None = None

    @property
    def model_id(self) -> str:
        return "test/hash-embedder"

    async def dimension(self) -> int:
        return DIM

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * DIM
        for t in tokens(text):
            v[zlib.crc32(t.encode()) % DIM] += 1.0
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / norm for x in v]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if self.before_call is not None:
            result = self.before_call()
            if hasattr(result, "__await__"):
                await result
        return [self._vec(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


class HashSparse:
    @property
    def model_id(self) -> str:
        return "test/hash-sparse"

    def _enc(self, text: str) -> SparseVector:
        counts: dict[int, float] = {}
        for t in tokens(text):
            i = zlib.crc32(t.encode()) % 100_000
            counts[i] = counts.get(i, 0.0) + 1.0
        return SparseVector(indices=list(counts), values=list(counts.values()))

    async def encode_documents(self, texts: list[str]) -> list[SparseVector]:
        return [self._enc(t) for t in texts]

    async def encode_query(self, text: str) -> SparseVector:
        return self._enc(text)


class WordCounter:
    def count(self, text: str) -> int:
        return len(text.split())


_STOP = {"the", "a", "an", "of", "to", "is", "are", "in", "for", "and", "what", "how", "do", "does", "we"}


class LexicalReranker:
    """Relevance = share of the query's content words found in the passage."""

    @property
    def model_id(self) -> str:
        return "test/lexical-reranker"

    async def score(self, query: str, passages: list[str]) -> list[float]:
        q = {t for t in tokens(query) if t not in _STOP}
        if not q:
            return [0.0 for _ in passages]
        return [len(q & set(tokens(p))) / len(q) for p in passages]


class ScriptedLLM:
    """LLMProvider that answers by citing the first passage of the given sources."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.condense_calls: list[str] = []
        self.script: Callable[[list[SourceDocument]], list[GenerationEvent]] | None = None
        self.fail_with: GenerationError | None = None

    @property
    def model_id(self) -> str:
        return "test/scripted-llm"

    async def stream_answer(
        self, *, question: str, sources: list[SourceDocument], history: list[ChatTurn]
    ) -> AsyncIterator[GenerationEvent]:
        self.calls.append({"question": question, "sources": sources, "history": history})
        if self.fail_with is not None:
            yield TextDelta("Partial ")
            raise self.fail_with
        events = self.script(sources) if self.script else self.default_script(sources)
        for event in events:
            yield event

    @staticmethod
    def default_script(sources: list[SourceDocument]) -> list[GenerationEvent]:
        first = sources[0].passages[0]
        return [
            TextDelta("According to the documents, "),
            TextDelta(first.rstrip(".").lower()),
            CitationDelta(source_index=0, cited_text=first, start_passage=0, end_passage=1),
            BlockEnd(),
            TextDelta("."),
            BlockEnd(),
            GenerationDone(model="test/scripted-llm", stop_reason="end_turn", usage={"output_tokens": 12}),
        ]

    async def condense_question(self, *, question: str, history: list[ChatTurn]) -> str:
        self.condense_calls.append(question)
        last_user = next((t.content for t in reversed(history) if t.role == "user"), "")
        return f"{question} (context: {last_user})"
