"""OpenAI models (e.g. gpt-5-nano) via the official SDK's Responses API, streamed.

OpenAI has no native document citations, so sources are given as numbered <source> blocks and the
model cites them with inline [n] markers. ``MarkerParser`` converts those into the same events as
Claude's native citations; ``CitationTracker`` then validates every marker against the sources we
supplied (unknown numbers are dropped and counted). Citations are therefore per chunk (not per
sentence as with Claude).

``store=False``: OpenAI is asked not to retain responses, since prompts contain document excerpts.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from html import escape
from typing import Any

import openai
import structlog

from app.core.config import Settings
from app.rag.generation.base import (
    ChatTurn,
    GenerationDone,
    GenerationError,
    GenerationEvent,
    SourceDocument,
)
from app.rag.generation.markers import MarkerParser
from app.rag.generation.prompts import CONDENSE_SYSTEM_PROMPT, MARKER_ANSWER_SYSTEM_PROMPT

log = structlog.get_logger(__name__)


def build_sources_text(question: str, sources: list[SourceDocument]) -> str:
    """Numbered sources in tags. Excerpt text is escaped so it cannot close or forge a tag."""
    blocks = []
    for number, source in enumerate(sources, start=1):
        body = escape("\n".join(source.passages), quote=False)
        title = escape(source.title, quote=True)
        note = f"\n(note: {escape(source.context, quote=False)})" if source.context else ""
        blocks.append(f'<source id="{number}" title="{title}">\n{body}{note}\n</source>')
    return "<sources>\n" + "\n\n".join(blocks) + "\n</sources>\n\nQuestion: " + question


def _history_input(history: list[ChatTurn]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for turn in history:
        if turn.content.strip():
            items.append({"role": turn.role, "content": turn.content})
    while items and items[0]["role"] != "user":
        items.pop(0)
    if items and items[-1]["role"] == "user":
        items.pop()  # an unanswered user turn; the new question follows
    return items


def _usage(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    out: dict[str, Any] = {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens}
    details = getattr(usage, "output_tokens_details", None)
    if details is not None and getattr(details, "reasoning_tokens", None) is not None:
        out["reasoning_tokens"] = details.reasoning_tokens
    cached = getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", None)
    if cached:
        out["cache_read_input_tokens"] = cached
    return out


def _to_generation_error(exc: Exception) -> GenerationError:
    if isinstance(exc, openai.AuthenticationError | openai.PermissionDeniedError):
        return GenerationError("llm_auth", "The language model is not configured correctly")
    if isinstance(exc, openai.RateLimitError):
        return GenerationError("llm_rate_limited", "The language model is busy; please retry", retryable=True)
    if isinstance(exc, openai.BadRequestError | openai.NotFoundError):
        return GenerationError("llm_bad_request", "The language model rejected the request")
    if isinstance(exc, openai.APIStatusError) and exc.status_code >= 500:
        return GenerationError("llm_unavailable", "The language model is temporarily unavailable", retryable=True)
    if isinstance(exc, openai.APIConnectionError | openai.APITimeoutError):
        return GenerationError("llm_unreachable", "Could not reach the language model", retryable=True)
    return GenerationError("llm_error", "The language model request failed")


class OpenAILLM:
    def __init__(self, settings: Settings, client: openai.AsyncOpenAI | None = None) -> None:
        if client is None:
            if settings.openai_api_key is None:
                raise ValueError("OPENAI_API_KEY is not set")
            client = openai.AsyncOpenAI(
                api_key=settings.openai_api_key.get_secret_value(),
                timeout=settings.llm_timeout_seconds,
                max_retries=2,
            )
        self.client = client
        self.settings = settings

    @property
    def model_id(self) -> str:
        return self.settings.llm_model

    async def stream_answer(
        self, *, question: str, sources: list[SourceDocument], history: list[ChatTurn]
    ) -> AsyncIterator[GenerationEvent]:
        s = self.settings
        parser = MarkerParser(sources)
        final: Any = None
        refused = False
        try:
            stream = await self.client.responses.create(
                model=s.llm_model,
                instructions=MARKER_ANSWER_SYSTEM_PROMPT,
                input=[
                    *_history_input(history),
                    {"role": "user", "content": build_sources_text(question, sources)},
                ],
                max_output_tokens=s.llm_max_tokens,  # includes reasoning tokens
                reasoning={"effort": s.llm_effort},
                store=False,
                stream=True,
            )
            async for event in stream:
                kind = event.type
                if kind == "response.output_text.delta":
                    for out in parser.feed(event.delta):
                        yield out
                elif kind == "response.refusal.delta":
                    refused = True
                elif kind in ("response.completed", "response.incomplete"):
                    final = event.response
                elif kind == "response.failed":
                    log.warning("llm_response_failed", error=str(getattr(event.response, "error", None))[:200])
                    raise GenerationError("llm_error", "The language model request failed")
                elif kind == "error":
                    log.warning("llm_stream_error", code=getattr(event, "code", None))
                    raise GenerationError("llm_error", "The language model request failed")
        except openai.APIError as exc:
            log.warning("llm_stream_failed", error=type(exc).__name__, request_id=getattr(exc, "request_id", None))
            raise _to_generation_error(exc) from exc

        for out in parser.flush():
            yield out

        stop_reason = "end_turn"
        if refused:
            stop_reason = "refusal"
        elif final is not None and final.status == "incomplete":
            reason = getattr(final.incomplete_details, "reason", None)
            stop_reason = "max_tokens" if reason == "max_output_tokens" else str(reason or "incomplete")
        yield GenerationDone(
            model=getattr(final, "model", None) or s.llm_model,
            stop_reason=stop_reason,
            usage=_usage(getattr(final, "usage", None)),
        )

    async def condense_question(self, *, question: str, history: list[ChatTurn]) -> str:
        transcript = "\n".join(f"{t.role.upper()}: {t.content[:2000]}" for t in history[-6:])
        try:
            response = await self.client.responses.create(
                model=self.settings.llm_model,
                instructions=CONDENSE_SYSTEM_PROMPT,
                input=f"Conversation:\n{transcript}\n\nLatest message: {question}\n\nStandalone query:",
                max_output_tokens=2000,  # reasoning tokens count toward this limit
                reasoning={
                    "effort": "low" if self.settings.llm_effort not in ("none", "minimal") else self.settings.llm_effort
                },
                store=False,
            )
        except openai.APIError as exc:
            log.warning("condense_failed", error=type(exc).__name__)
            return question  # retrieval with the raw question is a safe fallback
        text = (response.output_text or "").strip().strip('"')
        return text[:1000] or question
