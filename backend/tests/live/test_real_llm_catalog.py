"""Every model in the catalog, through the real adapters: routing JSON, a chat reply and a cited document answer.

Opt-in (it calls the providers and costs a few cents): RUN_LLM_TESTS=1, with the provider keys in the environment
or in the repository's .env. A model whose provider has no key, or whose key is out of quota, is *skipped* with
the provider's own message, so the run shows what is usable on this installation without failing on it.

    cd backend && RUN_LLM_TESTS=1 pytest tests/live -s
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.rag.answering import CitationTracker, to_source_documents
from app.rag.generation.base import GenerationDone, GenerationError, LLMProvider, TextDelta
from app.rag.generation.catalog import load_catalog
from app.rag.generation.prompts import ROUTER_SYSTEM_PROMPT
from app.rag.generation.registry import ModelRegistry
from app.rag.retrieval import RetrievedChunk
from app.rag.routing import router_prompt, router_schema

pytestmark = pytest.mark.skipif(not os.environ.get("RUN_LLM_TESTS"), reason="RUN_LLM_TESTS not set")

# Provider problems that say nothing about our adapter: no credit, a free-tier plan, a bad key, an outage.
NOT_OUR_FAULT = ("llm_quota_exceeded", "llm_auth", "llm_unavailable", "llm_unreachable", "llm_rate_limited")

CHUNK = RetrievedChunk(
    chunk_id=uuid.uuid4(),
    document_id=uuid.uuid4(),
    document_title="Security Policy.docx > Passwords",
    file_type="docx",
    kind="text",
    text="Passwords must be at least 14 characters long and rotated every 90 days.",
    token_count=14,
    index_version=1,
)


@pytest.fixture(scope="module")
def registry() -> ModelRegistry:
    try:
        settings = Settings()
    except ValidationError:
        pytest.skip("no usable configuration (DATABASE_URL / JWT_SECRET_KEY) for loading the API keys")
    settings.llm_effort = "low"
    return ModelRegistry(settings)


def provider_for(registry: ModelRegistry, model_id: str) -> LLMProvider:
    if model_id not in {o.spec.id for o in registry.available()}:
        pytest.skip(f"no API key configured for {model_id}'s provider")
    return registry.get(model_id)


async def collect(stream: Any) -> tuple[str, GenerationDone | None]:
    text, done = "", None
    async for event in stream:
        if isinstance(event, TextDelta):
            text += event.text
        elif isinstance(event, GenerationDone):
            done = event
    return text, done


@pytest.mark.parametrize("model_id", [m.id for m in load_catalog().models])
async def test_catalog_model_works_end_to_end(registry: ModelRegistry, model_id: str) -> None:
    llm = provider_for(registry, model_id)
    try:
        # 1. Routing: a structured-output call that reports its token usage.
        # (Called directly: route_message swallows provider errors and falls back to document search.)
        routing_usage: dict[str, Any] = {}
        routed = await llm.complete_json(
            system=ROUTER_SYSTEM_PROMPT,
            prompt=router_prompt("What can you do?", [], scoped=False),
            schema=router_schema(),
            name="route_message",
            max_tokens=2000,
            effort="low",
            usage_out=routing_usage,
        )
        assert routed["route"] == "chat" and routing_usage["input_tokens"] > 0

        # 2. A conversational reply, streamed, with usage that prices to a positive cost.
        text, done = await collect(llm.stream_chat(question="Hello!", history=[]))
        assert 0 < len(text.strip()) < 800 and done is not None and done.stop_reason == "end_turn"
        assert done.usage["input_tokens"] > 0 and done.usage["output_tokens"] > 0
        cost = registry.cost(model_id, done.usage)
        assert cost is not None and cost > 0, "every catalog model must be priced"

        # 3. A grounded answer: citations are produced and validated against the supplied source.
        sources = to_source_documents([CHUNK])
        tracker = CitationTracker([CHUNK], sources)
        async for event in llm.stream_answer(
            question="How often must passwords be rotated?", sources=sources, history=[]
        ):
            for _ in tracker.feed(event):
                pass
        assert "90" in tracker.answer and len(tracker.ordered_citations) >= 1 and tracker.rejected == 0
        assert tracker.done is not None and tracker.done.usage["input_tokens"] > 0
    except GenerationError as exc:
        if exc.code in NOT_OUR_FAULT:
            pytest.skip(f"{model_id}: {exc.code}: {exc.message}")
        raise
