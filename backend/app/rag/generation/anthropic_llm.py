"""Claude via the official Anthropic SDK: streaming, adaptive thinking, native citations.

Each retrieved chunk is sent as its own ``document`` block with custom content (one content block
per passage) and ``citations.enabled``. Claude's ``content_block_location`` citations therefore
reference (document_index, passage range) of material *we* supplied — a citation cannot point at
text that was not retrieved.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import anthropic
import structlog

from app.core.config import Settings
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
from app.rag.generation.prompts import ANSWER_SYSTEM_PROMPT, CONDENSE_SYSTEM_PROMPT

log = structlog.get_logger(__name__)

_FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _history_messages(history: list[ChatTurn]) -> list[dict[str, Any]]:
    """Alternating user/assistant turns starting with a user turn (as the API requires)."""
    messages: list[dict[str, Any]] = []
    for turn in history:
        if not turn.content.strip():
            continue
        if messages and messages[-1]["role"] == turn.role:
            messages[-1]["content"] += "\n\n" + turn.content
        elif messages or turn.role == "user":
            messages.append({"role": turn.role, "content": turn.content})
    if messages and messages[-1]["role"] == "user":
        messages.pop()  # an unanswered user turn; the new question follows
    return messages


def build_answer_content(question: str, sources: list[SourceDocument]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for source in sources:
        block: dict[str, Any] = {
            "type": "document",
            "source": {"type": "content", "content": [{"type": "text", "text": p} for p in source.passages]},
            "title": source.title[:500],
            "citations": {"enabled": True},
        }
        if source.context:
            block["context"] = source.context
        blocks.append(block)
    blocks.append({"type": "text", "text": f"Question: {question}"})
    return blocks


def _usage(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    fields = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    return {f: getattr(usage, f, None) for f in fields if getattr(usage, f, None) is not None}


def _to_generation_error(exc: Exception) -> GenerationError:
    if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
        return GenerationError("llm_auth", "The language model is not configured correctly")
    if isinstance(exc, anthropic.RateLimitError):
        return GenerationError("llm_rate_limited", "The language model is busy; please retry", retryable=True)
    if isinstance(exc, anthropic.BadRequestError):
        return GenerationError("llm_bad_request", "The language model rejected the request")
    if isinstance(exc, anthropic.APIStatusError) and exc.status_code >= 500:
        return GenerationError("llm_unavailable", "The language model is temporarily unavailable", retryable=True)
    if isinstance(exc, anthropic.APIConnectionError | anthropic.APITimeoutError):
        return GenerationError("llm_unreachable", "Could not reach the language model", retryable=True)
    return GenerationError("llm_error", "The language model request failed")


class AnthropicLLM:
    def __init__(self, settings: Settings, client: anthropic.AsyncAnthropic | None = None) -> None:
        if client is None:
            if settings.anthropic_api_key is None:
                raise ValueError("ANTHROPIC_API_KEY is not set")
            client = anthropic.AsyncAnthropic(
                api_key=settings.anthropic_api_key.get_secret_value(),
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
        kwargs: dict[str, Any] = {
            "model": s.llm_model,
            "max_tokens": s.llm_max_tokens,
            # Static system prompt first and marked cacheable; retrieved content goes in the user turn.
            "system": [{"type": "text", "text": ANSWER_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            "messages": [
                *_history_messages(history),
                {"role": "user", "content": build_answer_content(question, sources)},
            ],
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": "low" if s.llm_effort in ("none", "minimal") else s.llm_effort},
        }
        try:
            if s.llm_refusal_fallback:
                manager = self.client.beta.messages.stream(**kwargs, betas=[_FALLBACK_BETA], fallbacks="default")
            else:
                manager = self.client.messages.stream(**kwargs)
            async with manager as stream:
                async for event in stream:
                    if event.type == "content_block_delta":
                        delta = event.delta
                        if delta.type == "text_delta":
                            yield TextDelta(delta.text)
                        elif delta.type == "citations_delta":
                            c = delta.citation
                            if getattr(c, "type", None) == "content_block_location":
                                yield CitationDelta(
                                    source_index=c.document_index,
                                    cited_text=c.cited_text,
                                    start_passage=c.start_block_index,
                                    end_passage=c.end_block_index,
                                )
                    elif event.type == "content_block_stop":
                        yield BlockEnd()
                final = await stream.get_final_message()
        except anthropic.APIError as exc:
            log.warning("llm_stream_failed", error=type(exc).__name__, request_id=getattr(exc, "request_id", None))
            raise _to_generation_error(exc) from exc

        if final.stop_reason == "refusal":
            details = getattr(final, "stop_details", None)
            log.info("llm_refusal", category=getattr(details, "category", None))
        yield GenerationDone(model=final.model, stop_reason=final.stop_reason, usage=_usage(final.usage))

    async def complete_json(
        self, *, system: str, prompt: str, schema: dict[str, Any], name: str, max_tokens: int = 8000
    ) -> dict[str, Any]:
        effort = "low" if self.settings.llm_effort in ("none", "minimal") else self.settings.llm_effort
        try:
            response = await self.client.messages.create(
                model=self.settings.llm_model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            )
        except anthropic.APIError as exc:
            log.warning("llm_json_failed", task=name, error=type(exc).__name__)
            raise _to_generation_error(exc) from exc
        if response.stop_reason == "refusal":
            raise GenerationError("llm_refused", "The language model declined this request")
        if response.stop_reason == "max_tokens":
            raise GenerationError("llm_truncated", "The language model response was cut off")
        text = "".join(b.text for b in response.content if b.type == "text")
        try:
            return json.loads(text)  # type: ignore[no-any-return]
        except json.JSONDecodeError as exc:
            raise GenerationError("llm_invalid_output", "The language model returned invalid JSON") from exc

    async def condense_question(self, *, question: str, history: list[ChatTurn]) -> str:
        """Rewrite a follow-up ("what about the second one?") into a standalone search query."""
        transcript = "\n".join(f"{t.role.upper()}: {t.content[:2000]}" for t in history[-6:])
        try:
            response = await self.client.messages.create(
                model=self.settings.llm_model,
                max_tokens=1024,
                system=CONDENSE_SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": f"Conversation:\n{transcript}\n\nLatest message: {question}\n\nStandalone query:",
                    }
                ],
                output_config={"effort": "low"},
            )
        except anthropic.APIError as exc:
            log.warning("condense_failed", error=type(exc).__name__)
            return question  # retrieval with the raw question is a safe fallback
        if response.stop_reason == "refusal":
            return question
        text = "".join(b.text for b in response.content if b.type == "text").strip().strip('"')
        return text[:1000] or question
