"""Gemini adapter (OpenAI-compatible Chat Completions) and the provider-independent usage shape. No network."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import openai
import pytest

from app.rag.answering import CitationTracker, to_source_documents
from app.rag.generation import anthropic_llm
from app.rag.generation.base import ChatTurn, CitationDelta, GenerationDone, GenerationError, TextDelta
from app.rag.generation.google_llm import GeminiLLM, _effort, _usage
from app.rag.generation.openai_llm import _usage as openai_usage
from tests.helpers import make_settings
from tests.unit.test_openai_llm import CHUNKS, SOURCES, text_of


def chunk(
    content: str | None = None, finish: str | None = None, usage: Any = None, model: str = "gemini-3.6-flash"
) -> Any:
    choices = [SimpleNamespace(delta=SimpleNamespace(content=content), finish_reason=finish)]
    return SimpleNamespace(choices=choices, usage=usage, model=model)


def usage_chunk(prompt: int, completion: int, total: int, cached: int | None = None) -> Any:
    details = SimpleNamespace(cached_tokens=cached) if cached is not None else None
    return SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(
            prompt_tokens=prompt, completion_tokens=completion, total_tokens=total, prompt_tokens_details=details
        ),
        model="gemini-3.6-flash",
    )


class FakeStream:
    def __init__(self, chunks: list[Any]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> FakeStream:
        self._it = iter(self._chunks)
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration from None


class FakeCompletions:
    def __init__(self, chunks: list[Any] | None = None, error: Exception | None = None, message: str = "") -> None:
        self.chunks, self.error, self.message, self.calls = chunks or [], error, message, []
        self.finish = "stop"

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        if kwargs.get("stream"):
            return FakeStream(self.chunks)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.message), finish_reason=self.finish)],
            usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10, total_tokens=130, prompt_tokens_details=None),
        )


def gemini(completions: FakeCompletions, **settings: Any) -> GeminiLLM:
    s = make_settings(llm_provider="google", llm_model="gemini-3.6-flash", google_api_key="g", **settings)
    return GeminiLLM(s, client=SimpleNamespace(chat=SimpleNamespace(completions=completions)))  # type: ignore[arg-type]


# --- usage --------------------------------------------------------------------------------------


def test_thinking_tokens_are_counted_as_output() -> None:
    """Gemini's completion_tokens omits thinking tokens, which are billed as output: total - prompt covers both."""
    usage = _usage(SimpleNamespace(prompt_tokens=4, completion_tokens=9, total_tokens=138, prompt_tokens_details=None))
    assert usage == {"input_tokens": 4, "output_tokens": 134, "reasoning_tokens": 125}


def test_no_thinking_and_cached_tokens() -> None:
    plain = SimpleNamespace(prompt_tokens=12, completion_tokens=7, total_tokens=19, prompt_tokens_details=None)
    assert _usage(plain) == {"input_tokens": 12, "output_tokens": 7}
    cached = SimpleNamespace(
        prompt_tokens=500,
        completion_tokens=7,
        total_tokens=507,
        prompt_tokens_details=SimpleNamespace(cached_tokens=400),
    )
    assert _usage(cached)["cache_read_input_tokens"] == 400
    assert _usage(None) == {}


def test_claude_usage_input_tokens_is_the_whole_prompt() -> None:
    """Claude reports cache reads and writes outside input_tokens; they are folded in so costs add up."""
    usage = SimpleNamespace(
        input_tokens=10, output_tokens=5, cache_read_input_tokens=100, cache_creation_input_tokens=20
    )
    assert anthropic_llm._usage(usage) == {
        "input_tokens": 130,
        "output_tokens": 5,
        "cache_read_input_tokens": 100,
        "cache_creation_input_tokens": 20,
    }
    assert anthropic_llm._usage(SimpleNamespace(input_tokens=7, output_tokens=2)) == {
        "input_tokens": 7,
        "output_tokens": 2,
    }


def test_openai_usage_already_has_the_same_shape() -> None:
    usage = SimpleNamespace(
        input_tokens=120,
        output_tokens=40,
        output_tokens_details=SimpleNamespace(reasoning_tokens=25),
        input_tokens_details=SimpleNamespace(cached_tokens=100),
    )
    assert openai_usage(usage) == {
        "input_tokens": 120,
        "output_tokens": 40,
        "reasoning_tokens": 25,
        "cache_read_input_tokens": 100,
    }


@pytest.mark.parametrize(
    ("configured", "light", "expected"),
    [
        ("medium", False, "medium"),
        ("none", False, "minimal"),
        ("max", False, "high"),
        ("high", True, "low"),
        ("none", True, "minimal"),
    ],
)
def test_reasoning_effort_mapping(configured: str, light: bool, expected: str) -> None:
    assert _effort(configured, light=light) == expected


# --- streaming ----------------------------------------------------------------------------------


async def test_stream_chat_is_plain_text_with_usage_and_request_shape() -> None:
    fake = FakeCompletions([chunk("Hey! Option ["), chunk("1] is fine.", finish="stop"), usage_chunk(30, 8, 130)])
    history = [ChatTurn("user", "Passwords?"), ChatTurn("assistant", "Every 90 days [1].")]
    events = [e async for e in gemini(fake, llm_effort="medium").stream_chat(question="Hello!", history=history)]
    assert text_of(events) == "Hey! Option [1] is fine."  # brackets stay text: no sources, no citations
    assert not any(isinstance(e, CitationDelta) for e in events)
    done = events[-1]
    assert isinstance(done, GenerationDone) and done.stop_reason == "end_turn"
    assert done.model == "gemini-3.6-flash"
    assert done.usage == {"input_tokens": 30, "output_tokens": 100, "reasoning_tokens": 92}

    call = fake.calls[0]
    assert call["model"] == "gemini-3.6-flash" and call["stream"] is True
    assert call["stream_options"] == {"include_usage": True}
    assert call["reasoning_effort"] == "low"  # a conversational reply doesn't need medium reasoning
    assert [m["role"] for m in call["messages"]] == ["system", "user", "assistant", "user"]
    assert call["messages"][0]["content"].startswith("You are DocuMind")
    assert call["messages"][-1] == {"role": "user", "content": "Hello!"}


async def test_stream_answer_uses_marker_citations_validated_by_the_tracker() -> None:
    fake = FakeCompletions(
        [chunk("Every 90 days ["), chunk("1]. Also [9]."), chunk(None, finish="stop"), usage_chunk(500, 20, 520)]
    )
    model = gemini(fake, llm_effort="high")
    events = [e async for e in model.stream_answer(question="How often?", sources=SOURCES, history=[])]
    assert [e.source_index for e in events if isinstance(e, CitationDelta)] == [0, 8]
    tracker = CitationTracker(CHUNKS, to_source_documents(CHUNKS))
    for event in events:
        for _ in tracker.feed(event):
            pass
    assert [c.number for c in tracker.ordered_citations] == [1] and tracker.rejected == 1  # [9] was never supplied
    call = fake.calls[0]
    assert call["reasoning_effort"] == "high"
    assert "add its number in square brackets" in call["messages"][0]["content"]
    assert '<source id="1"' in call["messages"][-1]["content"]


async def test_finish_reasons_map_to_stop_reasons() -> None:
    for finish, expected in (("length", "max_tokens"), ("content_filter", "refusal"), ("stop", "end_turn")):
        fake = FakeCompletions([chunk("x", finish=finish)])
        events = [e async for e in gemini(fake).stream_chat(question="q", history=[])]
        assert events[-1].stop_reason == expected  # type: ignore[union-attr]
        assert isinstance(events[0], TextDelta)


# --- structured output --------------------------------------------------------------------------

SCHEMA = {
    "type": "object",
    "properties": {"route": {"type": "string"}},
    "required": ["route"],
    "additionalProperties": False,
}
JSON_KWARGS: dict[str, Any] = {"system": "s", "prompt": "p", "schema": SCHEMA, "name": "route_message"}


async def test_complete_json_returns_parsed_output_and_reports_usage() -> None:
    fake = FakeCompletions(message='{"route": "chat"}')
    usage: dict[str, Any] = {}
    out = await gemini(fake, llm_effort="high").complete_json(**JSON_KWARGS, effort="low", usage_out=usage)
    assert out == {"route": "chat"}
    assert usage == {"input_tokens": 20, "output_tokens": 110, "reasoning_tokens": 100}
    call = fake.calls[0]
    assert call["reasoning_effort"] == "low"
    assert call["response_format"]["json_schema"] == {"name": "route_message", "schema": SCHEMA, "strict": True}
    await gemini(fake, llm_effort="high").complete_json(**JSON_KWARGS)
    assert fake.calls[1]["reasoning_effort"] == "high"  # no override: the configured effort


@pytest.mark.parametrize(
    ("finish", "message", "code"),
    [
        ("length", "{}", "llm_truncated"),
        ("content_filter", "", "llm_refused"),
        ("stop", "not json", "llm_invalid_output"),
    ],
)
async def test_complete_json_failures(finish: str, message: str, code: str) -> None:
    fake = FakeCompletions(message=message)
    fake.finish = finish
    with pytest.raises(GenerationError) as exc:
        await gemini(fake).complete_json(**JSON_KWARGS)
    assert exc.value.code == code


# --- errors -------------------------------------------------------------------------------------


def rate_limit(body: Any) -> openai.RateLimitError:
    req = SimpleNamespace(method="POST", url="https://generativelanguage.googleapis.com/v1beta/openai/", headers={})
    return openai.RateLimitError(
        "429",
        response=SimpleNamespace(status_code=429, request=req, headers={}),
        body=body,  # type: ignore[arg-type]
    )


async def test_a_model_the_plan_cannot_serve_is_not_reported_as_busy() -> None:
    free_tier = rate_limit(
        [{"error": {"message": "Quota exceeded for metric: ..._free_tier_requests, limit: 0, model: x"}}]
    )
    with pytest.raises(GenerationError) as exc:
        [e async for e in gemini(FakeCompletions(error=free_tier)).stream_chat(question="q", history=[])]
    assert exc.value.code == "llm_quota_exceeded" and not exc.value.retryable

    busy = rate_limit([{"error": {"message": "Too many requests per minute"}}])
    with pytest.raises(GenerationError) as exc:
        [e async for e in gemini(FakeCompletions(error=busy)).stream_chat(question="q", history=[])]
    assert exc.value.code == "llm_rate_limited" and exc.value.retryable


def test_missing_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="GOOGLE_API_KEY"):
        GeminiLLM(make_settings(llm_provider="google", llm_model="gemini-3.6-flash"))
