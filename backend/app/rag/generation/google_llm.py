"""Gemini through Google's OpenAI-compatible endpoint (Chat Completions), using the ``openai`` SDK we already ship.

No extra dependency. Differences from the OpenAI Responses adapter:

* Chat Completions API (system message instead of ``instructions``, ``stream_options`` for usage).
* Gemini has no native document citations, so sources are numbered ``<source>`` blocks cited with inline ``[n]``
  markers, validated by ``CitationTracker`` exactly as for OpenAI.
* ``completion_tokens`` leaves out the model's "thinking" tokens, which Google bills as output. ``total_tokens -
  prompt_tokens`` covers both, so that is what we report as output (otherwise costs would be understated).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import openai
import structlog

from app.core.config import Settings
from app.rag.generation.base import ChatTurn, GenerationDone, GenerationError, GenerationEvent, SourceDocument
from app.rag.generation.markers import MarkerParser
from app.rag.generation.openai_llm import (
    _history_input,
    _PlainText,
    _TextParser,
    _to_generation_error,
    build_sources_text,
)
from app.rag.generation.prompts import ASSISTANT_SYSTEM_PROMPT, MARKER_ANSWER_SYSTEM_PROMPT

log = structlog.get_logger(__name__)

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

# Gemini's reasoning_effort accepts minimal | low | medium | high.
_EFFORT = {"none": "minimal", "minimal": "minimal", "low": "low", "medium": "medium", "high": "high"}


def _effort(configured: str, *, light: bool = False) -> str:
    if light:
        return "minimal" if configured in ("none", "minimal") else "low"
    return _EFFORT.get(configured, "high")  # xhigh / max


def _usage(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    prompt = getattr(usage, "prompt_tokens", 0) or 0
    completion = getattr(usage, "completion_tokens", 0) or 0
    total = getattr(usage, "total_tokens", 0) or 0
    output = max(total - prompt, completion)  # includes thinking tokens
    out: dict[str, Any] = {"input_tokens": prompt, "output_tokens": output}
    if output > completion:
        out["reasoning_tokens"] = output - completion
    cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", None)
    if cached:
        out["cache_read_input_tokens"] = cached
    return out


class GeminiLLM:
    def __init__(self, settings: Settings, client: openai.AsyncOpenAI | None = None) -> None:
        if client is None:
            if settings.google_api_key is None:
                raise ValueError("GOOGLE_API_KEY is not set")
            client = openai.AsyncOpenAI(
                api_key=settings.google_api_key.get_secret_value(),
                base_url=BASE_URL,
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
        messages = [
            {"role": "system", "content": MARKER_ANSWER_SYSTEM_PROMPT},
            *_history_input(history),
            {"role": "user", "content": build_sources_text(question, sources)},
        ]
        return self._stream(messages, MarkerParser(sources), _effort(self.settings.llm_effort))

    def stream_chat(self, *, question: str, history: list[ChatTurn]) -> AsyncIterator[GenerationEvent]:
        messages = [
            {"role": "system", "content": ASSISTANT_SYSTEM_PROMPT},
            *_history_input(history),
            {"role": "user", "content": question},
        ]
        return self._stream(messages, _PlainText(), _effort(self.settings.llm_effort, light=True))

    async def _stream(
        self, messages: list[dict[str, Any]], parser: _TextParser, effort: str
    ) -> AsyncIterator[GenerationEvent]:
        s = self.settings
        request: dict[str, Any] = {
            "model": s.llm_model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_completion_tokens": s.llm_max_tokens,  # includes thinking tokens
            "reasoning_effort": effort,
        }
        finish: str | None = None
        usage: Any = None
        served_model = s.llm_model
        try:
            stream = await self.client.chat.completions.create(**request)
            async for chunk in stream:
                if chunk.choices:
                    choice = chunk.choices[0]
                    if choice.delta is not None and choice.delta.content:
                        for out in parser.feed(choice.delta.content):
                            yield out
                    finish = choice.finish_reason or finish
                if getattr(chunk, "usage", None):
                    usage = chunk.usage
                served_model = getattr(chunk, "model", None) or served_model
        except openai.APIError as exc:
            log.warning("llm_stream_failed", error=type(exc).__name__, request_id=getattr(exc, "request_id", None))
            raise _to_generation_error(exc) from exc

        for out in parser.flush():
            yield out
        stop_reason = {"stop": "end_turn", "length": "max_tokens", "content_filter": "refusal"}.get(
            finish or "stop", finish or "end_turn"
        )
        yield GenerationDone(model=served_model, stop_reason=stop_reason, usage=_usage(usage))

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
        request: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "max_completion_tokens": max_tokens,
            "reasoning_effort": _effort(effort or self.settings.llm_effort),
            "response_format": {"type": "json_schema", "json_schema": {"name": name, "schema": schema, "strict": True}},
        }
        try:
            response = await self.client.chat.completions.create(**request)
        except openai.APIError as exc:
            log.warning("llm_json_failed", task=name, error=type(exc).__name__)
            raise _to_generation_error(exc) from exc
        if usage_out is not None:
            usage_out.update(_usage(response.usage))
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise GenerationError("llm_truncated", "The language model response was cut off")
        if choice.finish_reason == "content_filter":
            raise GenerationError("llm_refused", "The language model declined this request")
        try:
            return json.loads(choice.message.content or "")  # type: ignore[no-any-return]
        except json.JSONDecodeError as exc:
            raise GenerationError("llm_invalid_output", "The language model returned invalid JSON") from exc
