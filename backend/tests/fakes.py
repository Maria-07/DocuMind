"""Deterministic test doubles implementing the real provider protocols (no network, no models).

They exercise all pipeline/retrieval/chat logic exactly; real models and the real LLM are covered
by the opt-in tests (test_real_models.py, test_real_answers.py).
"""

from __future__ import annotations

import html
import re
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

# Token usage the scripted LLM reports (so tests can check totals and costs).
ANSWER_USAGE = {"input_tokens": 1000, "output_tokens": 200}
CHAT_USAGE = {"input_tokens": 300, "output_tokens": 50}
ROUTING_USAGE = {"input_tokens": 100, "output_tokens": 10}


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
    """LLMProvider that answers by citing the first passage of the given sources.

    Routing (``complete_json`` name ``route_message``) defaults to "documents" with the message as the
    query (plus the last user turn as context for a follow-up); script it via ``json_responses``.
    Conversational replies (``stream_chat``) say ``chat_reply``.
    """

    def __init__(self, model_id: str = "test/scripted-llm") -> None:
        self._model_id = model_id
        self.calls: list[dict[str, Any]] = []
        self.chat_calls: list[dict[str, Any]] = []
        self.chat_reply = "Hello! How can I help you today?"
        self.script: Callable[[list[SourceDocument]], list[GenerationEvent]] | None = None
        self.fail_with: GenerationError | None = None
        self.json_calls: list[dict[str, Any]] = []
        self.json_responses: dict[str, Any] = {}

    @property
    def model_id(self) -> str:
        return self._model_id

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
            GenerationDone(model="test/scripted-llm", stop_reason="end_turn", usage=ANSWER_USAGE),
        ]

    async def stream_chat(self, *, question: str, history: list[ChatTurn]) -> AsyncIterator[GenerationEvent]:
        self.chat_calls.append({"question": question, "history": history})
        if self.fail_with is not None:
            yield TextDelta("Partial ")
            raise self.fail_with
        for word in self.chat_reply.split(" "):
            yield TextDelta(word + " ")
        yield GenerationDone(model=self._model_id, stop_reason="end_turn", usage=CHAT_USAGE)

    @staticmethod
    def default_route(prompt: str) -> dict[str, Any]:
        match = re.search(r"<latest_message>(.*?)</latest_message>", prompt, re.S)
        latest = html.unescape(match.group(1)) if match else prompt
        earlier = html.unescape(prompt.split("<latest_message>")[0])
        users = re.findall(r"^user: (.*)$", earlier, re.M)
        return {"route": "documents", "search_query": f"{latest} (context: {users[-1]})" if users else latest}

    async def complete_json(
        self,
        *,
        system: str,
        prompt: str,
        schema: dict[str, Any],
        name: str,
        max_tokens: int = 8000,
        effort: str | None = None,
        usage_out: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if usage_out is not None and name == "route_message":
            usage_out.update(ROUTING_USAGE)
        self.json_calls.append({"name": name, "prompt": prompt, "effort": effort})
        if name in self.json_responses:
            response = self.json_responses[name]
            return response(prompt) if callable(response) else response
        if name == "route_message":
            return self.default_route(prompt)
        if name == "answer_judgement":
            return {
                "claims": 2,
                "supported_claims": 2,
                "unsupported_claims": [],
                "answer_relevance": 5,
                "context_relevance": 0.5,
                "correctness": 4,
                "abstained": False,
                "rationale": "scripted",
            }
        raise GenerationError("llm_error", f"no scripted response for {name}")
