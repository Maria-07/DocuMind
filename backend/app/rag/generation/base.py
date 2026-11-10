"""Provider-independent generation interface."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass(frozen=True, slots=True)
class SourceDocument:
    """One retrieved chunk as presented to the model. ``passages`` are the citable units."""

    title: str
    passages: list[str]
    context: str | None = None


@dataclass(frozen=True, slots=True)
class ChatTurn:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class TextDelta:
    text: str


@dataclass(frozen=True, slots=True)
class CitationDelta:
    """The model cited ``passages[start:end]`` of source ``source_index``."""

    source_index: int
    cited_text: str
    start_passage: int | None = None
    end_passage: int | None = None


@dataclass(frozen=True, slots=True)
class BlockEnd:
    """End of a text segment; citations received since the last BlockEnd belong to it."""


@dataclass(frozen=True, slots=True)
class GenerationDone:
    model: str
    stop_reason: str | None
    # Token usage in the provider-independent shape of ``catalog.USAGE_COUNTERS``.
    usage: dict[str, Any] = field(default_factory=dict)

    @property
    def refused(self) -> bool:
        return self.stop_reason == "refusal"


GenerationEvent = TextDelta | CitationDelta | BlockEnd | GenerationDone


class GenerationError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class LLMProvider(Protocol):
    @property
    def model_id(self) -> str: ...

    def stream_answer(
        self, *, question: str, sources: list[SourceDocument], history: list[ChatTurn]
    ) -> AsyncIterator[GenerationEvent]:
        """A grounded answer from retrieved ``sources``, with citations."""
        ...

    def stream_chat(self, *, question: str, history: list[ChatTurn]) -> AsyncIterator[GenerationEvent]:
        """A conversational reply that needs no documents (greeting, thanks, general help).

        Yields ``TextDelta`` events and a final ``GenerationDone``; never citations.
        """
        ...

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
        """One non-streaming call whose output is constrained to ``schema`` (structured outputs).

        Schemas must be strict-compatible: every property listed in ``required``,
        ``additionalProperties: false``, optional values expressed as ``["type", "null"]``.
        ``effort`` overrides the configured reasoning effort (small classification calls pass "low").
        ``usage_out``, when given, receives the call's token usage (see ``catalog.USAGE_COUNTERS``).
        """
        ...
