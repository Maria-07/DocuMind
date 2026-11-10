"""Claude adapter against a fake client (no network): request shape, refusal fallbacks, usage, errors.

The adapter has not yet been run against the live API (the project's key hit its usage limit), so these tests pin
its request shapes to the documented ones.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anthropic
import pytest

from app.rag.generation.anthropic_llm import AnthropicLLM
from app.rag.generation.base import ChatTurn, CitationDelta, GenerationDone, GenerationError, TextDelta
from app.rag.generation.registry import ModelRegistry
from tests.helpers import make_settings
from tests.unit.test_openai_llm import SOURCES, text_of


def final_message(stop: str = "end_turn") -> Any:
    usage = SimpleNamespace(
        input_tokens=20, output_tokens=7, cache_read_input_tokens=1000, cache_creation_input_tokens=0
    )
    return SimpleNamespace(stop_reason=stop, model="claude-snapshot", usage=usage)


class FakeManager:
    def __init__(self, events: list[Any], final: Any) -> None:
        self._events, self._final = events, final

    async def __aenter__(self) -> FakeManager:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    def __aiter__(self) -> FakeManager:
        self._it = iter(self._events)
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration from None

    async def get_final_message(self) -> Any:
        return self._final


def text_event(text: str) -> Any:
    return SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text=text))


class FakeMessages:
    def __init__(self, events: list[Any] | None = None, create_result: Any = None, error: Exception | None = None):
        self.events = events if events is not None else [text_event("Hi"), SimpleNamespace(type="content_block_stop")]
        self.create_result, self.error, self.calls = create_result, error, []

    def stream(self, **kwargs: Any) -> FakeManager:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return FakeManager(self.events, final_message())

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.create_result


def claude(
    model: str, plain: FakeMessages | None = None, beta: FakeMessages | None = None, **settings: Any
) -> AnthropicLLM:
    s = make_settings(llm_provider="anthropic", llm_model=model, anthropic_api_key="k", **settings)
    client = SimpleNamespace(messages=plain or FakeMessages(), beta=SimpleNamespace(messages=beta or FakeMessages()))
    return AnthropicLLM(s, client=client)  # type: ignore[arg-type]


# --- refusal fallbacks only where Claude has them ------------------------------------------------


@pytest.mark.parametrize("model", ["claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5"])
async def test_models_with_a_fallback_use_the_beta_stream(model: str) -> None:
    plain, beta = FakeMessages(), FakeMessages()
    events = [e async for e in claude(model, plain, beta).stream_chat(question="Hello!", history=[])]
    assert plain.calls == [] and len(beta.calls) == 1
    call = beta.calls[0]
    assert call["betas"] == ["server-side-fallback-2026-07-01"] and call["fallbacks"] == "default"
    assert call["model"] == model and call["output_config"] == {"effort": "low"} and "thinking" not in call
    assert text_of(events) == "Hi"


async def test_haiku_has_no_refusal_fallback() -> None:
    plain, beta = FakeMessages(), FakeMessages()
    [e async for e in claude("claude-haiku-5-5", plain, beta).stream_chat(question="Hello!", history=[])]
    assert beta.calls == [] and len(plain.calls) == 1
    assert "betas" not in plain.calls[0] and "fallbacks" not in plain.calls[0]


async def test_the_fallback_can_be_switched_off() -> None:
    plain, beta = FakeMessages(), FakeMessages()
    [
        e
        async for e in claude("claude-opus-5-5", plain, beta, llm_refusal_fallback=False).stream_chat(
            question="q", history=[]
        )
    ]
    assert beta.calls == [] and len(plain.calls) == 1


# --- request shapes -----------------------------------------------------------------------------


async def test_stream_chat_request_and_canonical_usage() -> None:
    beta = FakeMessages()
    history = [ChatTurn("user", "Passwords?"), ChatTurn("assistant", "Every 90 days [1].")]
    events = [e async for e in claude("claude-opus-5-5", beta=beta).stream_chat(question="Hello!", history=history)]
    call = beta.calls[0]
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert (
        call["system"][0]["text"].startswith("You are DocuMind")
        and "No document search was done" in call["system"][0]["text"]
    )
    assert [m["role"] for m in call["messages"]] == ["user", "assistant", "user"]
    assert call["messages"][-1] == {"role": "user", "content": "Hello!"}
    done = events[-1]
    assert isinstance(done, GenerationDone) and done.model == "claude-snapshot"
    # Claude reports cache reads outside input_tokens; the canonical usage folds them in.
    assert done.usage == {"input_tokens": 1020, "output_tokens": 7, "cache_read_input_tokens": 1000}


async def test_stream_answer_sends_citable_documents_with_adaptive_thinking() -> None:
    beta = FakeMessages(events=[text_event("Every 90 days"), SimpleNamespace(type="content_block_stop")])
    llm = claude("claude-sonnet-5-5", beta=beta, llm_effort="none")
    events = [e async for e in llm.stream_answer(question="How often?", sources=SOURCES, history=[])]
    call = beta.calls[0]
    assert call["thinking"] == {"type": "adaptive"}
    assert call["output_config"] == {"effort": "low"}  # "none"/"minimal" exist for OpenAI only
    content = call["messages"][-1]["content"]
    assert [b["type"] for b in content] == ["document", "document", "text"]
    assert all(b["citations"] == {"enabled": True} for b in content[:2])
    assert "cite the passages" in call["system"][0]["text"]
    assert not any(isinstance(e, CitationDelta) for e in events) and isinstance(events[-1], GenerationDone)
    assert any(isinstance(e, TextDelta) for e in events)


async def test_complete_json_reports_usage_and_maps_stop_reasons() -> None:
    def reply(stop: str, text: str) -> Any:
        usage = SimpleNamespace(
            input_tokens=15, output_tokens=5, cache_read_input_tokens=None, cache_creation_input_tokens=None
        )
        return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="text", text=text)], usage=usage)

    schema = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    kwargs: dict[str, Any] = {"system": "s", "prompt": "p", "schema": schema, "name": "route_message"}
    plain = FakeMessages(create_result=reply("end_turn", '{"route": "chat"}'))
    usage: dict[str, Any] = {}
    out = await claude("claude-haiku-5-5", plain).complete_json(**kwargs, effort="low", usage_out=usage)
    assert out == {"route": "chat"} and usage == {"input_tokens": 15, "output_tokens": 5}
    sent = plain.calls[0]
    assert sent["output_config"]["effort"] == "low" and sent["output_config"]["format"]["schema"] == schema

    for stop, code in (("refusal", "llm_refused"), ("max_tokens", "llm_truncated")):
        with pytest.raises(GenerationError) as exc:
            await claude("claude-haiku-5-5", FakeMessages(create_result=reply(stop, "{}"))).complete_json(**kwargs)
        assert exc.value.code == code
    with pytest.raises(GenerationError) as exc:
        await claude("claude-haiku-5-5", FakeMessages(create_result=reply("end_turn", "nope"))).complete_json(**kwargs)
    assert exc.value.code == "llm_invalid_output"


# --- errors -------------------------------------------------------------------------------------


def status_error(cls: type[anthropic.APIStatusError], status: int, message: str) -> anthropic.APIStatusError:
    """Shaped like the real SDK error: ``message`` is the whole \"Error code\" string, the clean text is in the body."""
    req = SimpleNamespace(method="POST", url="https://api.anthropic.com/v1/messages", headers={})
    response = SimpleNamespace(status_code=status, request=req, headers={})
    body = {"type": "error", "error": {"type": "invalid_request_error", "message": message}}
    return cls(f"Error code: {status} - {body}", response=response, body=body)  # type: ignore[arg-type]


async def test_a_spent_usage_limit_is_reported_with_the_providers_message() -> None:
    limit = "You have reached your specified API usage limits. You will regain access on 2026-12-01 at 00:00 UTC."
    plain = FakeMessages(error=status_error(anthropic.BadRequestError, 400, limit))
    with pytest.raises(GenerationError) as exc:
        [e async for e in claude("claude-haiku-5-5", plain).stream_chat(question="q", history=[])]
    assert exc.value.code == "llm_quota_exceeded" and not exc.value.retryable
    assert exc.value.message == limit  # the provider's sentence, not the SDK's raw error string

    credit = status_error(anthropic.BadRequestError, 400, "Your credit balance is too low to access the API.")
    with pytest.raises(GenerationError) as exc:
        [e async for e in claude("claude-haiku-5-5", FakeMessages(error=credit)).stream_chat(question="q", history=[])]
    assert exc.value.code == "llm_quota_exceeded"


async def test_other_errors_keep_their_generic_codes() -> None:
    for err, code in (
        (status_error(anthropic.BadRequestError, 400, "messages: unexpected field"), "llm_bad_request"),
        (status_error(anthropic.RateLimitError, 429, "slow down"), "llm_rate_limited"),
        (status_error(anthropic.AuthenticationError, 401, "bad key"), "llm_auth"),
    ):
        with pytest.raises(GenerationError) as exc:
            [e async for e in claude("claude-haiku-5-5", FakeMessages(error=err)).stream_chat(question="q", history=[])]
        assert exc.value.code == code


# --- registry availability flag -------------------------------------------------------------------


def test_a_model_that_hit_its_limit_is_flagged_until_it_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    reg = ModelRegistry(make_settings(llm_provider="anthropic", llm_model="claude-haiku-5-5", anthropic_api_key="k"))
    flags = lambda: {o.spec.id: o.unavailable_reason for o in reg.available()}  # noqa: E731
    assert set(flags().values()) == {None}
    reg.mark_unavailable("claude-opus-5-5", "limit reached", seconds=60)
    assert flags()["claude-opus-5-5"] == "limit reached" and flags()["claude-haiku-5-5"] is None
    import app.rag.generation.registry as registry_module

    real = registry_module.time.monotonic()
    monkeypatch.setattr(registry_module.time, "monotonic", lambda: real + 120)
    assert flags()["claude-opus-5-5"] is None  # expired
    reg.mark_unavailable("claude-opus-5-5", "again")
    reg.clear_unavailable("claude-opus-5-5")
    assert reg.unavailable_reason("claude-opus-5-5") is None
