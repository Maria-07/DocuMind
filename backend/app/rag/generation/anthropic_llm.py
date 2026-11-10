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
from app.rag.generation.prompts import ANSWER_SYSTEM_PROMPT, ASSISTANT_SYSTEM_PROMPT

log = structlog.get_logger(__name__)

_FALLBACK_BETA = "server-side-fallback-2026-07-01"
# The server-side refusal fallback exists for these families; Claude Haiku 5.5 has none.
_FALLBACK_MODELS = ("claude-fable-5", "claude-opus-5", "claude-sonnet-5-5")


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
    """Claude reports cache reads/writes *outside* input_tokens; fold them in so input_tokens is the whole prompt."""
    if usage is None:
        return {}
    read = getattr(usage, "cache_read_input_tokens", None) or 0
    written = getattr(usage, "cache_creation_input_tokens", None) or 0
    out: dict[str, Any] = {
        "input_tokens": (getattr(usage, "input_tokens", None) or 0) + read + written,
        "output_tokens": getattr(usage, "output_tokens", None) or 0,
    }
    if read:
        out["cache_read_input_tokens"] = read
    if written:
        out["cache_creation_input_tokens"] = written
    return out


def _provider_message(exc: Exception) -> str:
    """The provider's own sentence. The SDK's ``message`` is the whole "Error code: 400 - {...}" string; the clean
    text is in the error body."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return str(error["message"])
    return str(getattr(exc, "message", "") or exc)


def _to_generation_error(exc: Exception) -> GenerationError:
    if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
        return GenerationError("llm_auth", "The language model is not configured correctly")
    if isinstance(exc, anthropic.RateLimitError):
        return GenerationError("llm_rate_limited", "The language model is busy; please retry", retryable=True)
    if isinstance(exc, anthropic.BadRequestError):
        detail = _provider_message(exc)
        # Spend limits and an empty credit balance arrive as 400s; the provider's own text says when
        # access returns, which is exactly what the user needs to see.
        if "usage limit" in detail.lower() or "credit balance" in detail.lower():
            return GenerationError("llm_quota_exceeded", detail[:240])
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

    def stream_answer(
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
        return self._stream(kwargs)

    def stream_chat(self, *, question: str, history: list[ChatTurn]) -> AsyncIterator[GenerationEvent]:
        s = self.settings
        kwargs: dict[str, Any] = {
            "model": s.llm_model,
            "max_tokens": s.llm_max_tokens,
            "system": [{"type": "text", "text": ASSISTANT_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            "messages": [*_history_messages(history), {"role": "user", "content": question}],
            "output_config": {"effort": "low"},  # a conversational reply needs little reasoning
        }
        return self._stream(kwargs)

    async def _stream(self, kwargs: dict[str, Any]) -> AsyncIterator[GenerationEvent]:
        s = self.settings
        try:
            manager: Any  # the beta (refusal fallback) and GA stream managers share this interface
            if s.llm_refusal_fallback and s.llm_model.startswith(_FALLBACK_MODELS):
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
                            if c.type == "content_block_location":
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
        effort = effort or self.settings.llm_effort
        if effort in ("none", "minimal"):
            effort = "low"  # those levels exist for OpenAI only
        try:
            # A plain dict: the SDK's typed overloads lag behind output_config.format.
            request: dict[str, Any] = {
                "model": self.settings.llm_model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
                "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            }
            response = await self.client.messages.create(**request)
        except anthropic.APIError as exc:
            log.warning("llm_json_failed", task=name, error=type(exc).__name__)
            raise _to_generation_error(exc) from exc
        if usage_out is not None:
            usage_out.update(_usage(response.usage))
        if response.stop_reason == "refusal":
            raise GenerationError("llm_refused", "The language model declined this request")
        if response.stop_reason == "max_tokens":
            raise GenerationError("llm_truncated", "The language model response was cut off")
        text = "".join(b.text for b in response.content if b.type == "text")
        try:
            return json.loads(text)  # type: ignore[no-any-return]
        except json.JSONDecodeError as exc:
            raise GenerationError("llm_invalid_output", "The language model returned invalid JSON") from exc
