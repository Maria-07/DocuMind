"""Retrieval + chat against real PostgreSQL + Qdrant, with deterministic embeddings/reranker/LLM."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import select, update

from app.core.resources import AppResources
from app.models import Document, Message, MessageCitation, RetrievalLog
from app.rag.generation.base import GenerationError
from app.rag.generation.catalog import Catalog, ModelSpec, Price
from app.rag.generation.registry import ModelRegistry
from app.rag.ingestion.pipeline import IngestionPipeline
from app.rag.runtime import build_rag_runtime
from tests.documents import build_docx, build_pdf, build_xlsx
from tests.fakes import HashSparse, LexicalReranker, ScriptedLLM
from tests.integration.api_helpers import Session, add_member, register, upload_one
from tests.integration.rag_helpers import run_ingestion

ClientFactory = Callable[[], httpx.AsyncClient]


@pytest.fixture
def llm() -> ScriptedLLM:
    return ScriptedLLM()


@pytest.fixture
def rag(resources: AppResources, pipeline: IngestionPipeline, embedder: Any, qdrant: Any, llm: ScriptedLLM) -> Any:
    resources.settings.retrieval_min_relevance = 0.3
    resources.rag = build_rag_runtime(
        resources.settings,
        resources.sessionmaker,
        qdrant,
        embedder=embedder,
        sparse=HashSparse(),
        reranker=LexicalReranker(),
        llm=llm,
    )
    return resources.rag


async def _ingest(
    session: Session, pipeline: IngestionPipeline, resources: AppResources, name: str, data: bytes, **form: str
) -> dict[str, Any]:
    doc = await upload_one(session, name, data, **form)
    assert await run_ingestion(pipeline, resources, doc["id"]) == "indexed"
    return doc


async def _search(s: Session, query: str, mode: str = "hybrid", **body: Any) -> list[dict[str, Any]]:
    resp = await s.client.post(f"/api/v1/search/{mode}", headers=s.headers, json={"query": query, **body})
    assert resp.status_code == 200, resp.text
    return list(resp.json()["results"])


def _sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n") if not line.startswith(":"))
        if "event" in lines:
            events.append((lines["event"], json.loads(lines["data"])))
    return events


async def _ask(s: Session, conversation_id: str, content: str, **body: Any) -> list[tuple[str, dict[str, Any]]]:
    resp = await s.client.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages",
        headers={**s.headers, "Accept": "text/event-stream"},
        json={"content": content, **body},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    return _sse(resp.text)


async def _new_conversation(s: Session, **body: Any) -> str:
    resp = await s.client.post("/api/v1/chat/conversations", headers=s.headers, json=body)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


# --- search & access control ---------------------------------------------------------------


async def test_hybrid_and_semantic_search_find_the_relevant_chunk(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources, rag: Any
) -> None:
    admin = await register(client)
    await _ingest(admin, pipeline, resources, "policy.docx", build_docx())
    for mode in ("hybrid", "semantic"):
        results = await _search(admin, "how often must passwords be rotated", mode)
        assert results[0]["section_path"] == "Security Policy > Access Control > Passwords"
        assert results[0]["relevance"] is not None and results[0]["location"]
    async with resources.sessionmaker() as s:
        logs = (await s.execute(select(RetrievalLog))).scalars().all()
    assert len(logs) == 2 and all(log.source == "search" and log.returned_chunk_ids for log in logs)


async def test_search_never_returns_unauthorized_chunks(
    client: httpx.AsyncClient,
    make_client: ClientFactory,
    pipeline: IngestionPipeline,
    resources: AppResources,
    rag: Any,
) -> None:
    admin = await register(client)
    alice = await add_member(admin, make_client())
    outsider = await register(make_client(), org="Other Corp")
    await _ingest(
        admin,
        pipeline,
        resources,
        "salaries.pdf",
        build_pdf(["Executive salaries are confidential compensation figures. " * 5]),
        visibility="private",
    )
    await _ingest(
        admin,
        pipeline,
        resources,
        "handbook.pdf",
        build_pdf(["The employee handbook explains compensation reviews. " * 5]),
    )

    query = "compensation salaries"
    assert {r["document_title"] for r in await _search(admin, query)} == {"salaries", "handbook"}
    assert {r["document_title"] for r in await _search(alice, query)} == {"handbook"}
    assert await _search(outsider, query) == []


async def test_postcheck_drops_hits_that_qdrant_still_has(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources, rag: Any
) -> None:
    """Defense in depth: PostgreSQL decides even if the vector payload is stale."""
    admin = await register(client)
    gone = await _ingest(
        admin, pipeline, resources, "gone.pdf", build_pdf(["Quarterly revenue forecast details. " * 5])
    )
    stale = await _ingest(
        admin, pipeline, resources, "stale.pdf", build_pdf(["Quarterly revenue actual numbers. " * 5])
    )
    async with resources.sessionmaker() as s:
        # Deleted in PG without touching Qdrant; and an index version that no longer matches.
        await s.execute(
            update(Document).where(Document.id == uuid.UUID(gone["id"])).values(deleted_at=Document.created_at)
        )
        await s.execute(update(Document).where(Document.id == uuid.UUID(stale["id"])).values(active_index_version=99))
        await s.commit()
    points, _ = await pipeline.store.client.scroll(pipeline.collection, with_payload=True, limit=100)
    assert all(p.payload["searchable"] for p in points)  # Qdrant would still return them

    assert await _search(admin, "quarterly revenue") == []
    async with resources.sessionmaker() as s:
        log = (await s.execute(select(RetrievalLog))).scalars().one()
    assert log.filtered_out >= 2 and log.candidate_count >= 2


async def test_scope_filters_and_document_search(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources, rag: Any
) -> None:
    admin = await register(client)
    policy = await _ingest(admin, pipeline, resources, "policy.docx", build_docx())
    await _ingest(admin, pipeline, resources, "revenue.xlsx", build_xlsx(rows=8))
    results = await _search(admin, "retention records", filters={"document_ids": [policy["id"]]})
    assert results and {r["document_id"] for r in results} == {policy["id"]}
    only_xlsx = await _search(admin, "revenue region", filters={"file_types": ["xlsx"]})
    assert only_xlsx and {r["file_type"] for r in only_xlsx} == {"xlsx"}

    resp = await admin.client.post("/api/v1/search/documents", headers=admin.headers, json={"query": "EMEA revenue"})
    hits = resp.json()["results"]
    assert hits[0]["document_title"] == "revenue" and hits[0]["matching_chunks"] >= 1


# --- chat ----------------------------------------------------------------------------------


async def test_chat_streams_grounded_answer_and_persists_it(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources, rag: Any, llm: ScriptedLLM
) -> None:
    admin = await register(client)
    await _ingest(admin, pipeline, resources, "policy.docx", build_docx())
    conv = await _new_conversation(admin)

    events = await _ask(admin, conv, "How often must passwords be rotated?")
    names = [n for n, _ in events]
    assert names[0] == "meta" and names[-1] == "done"
    assert names.index("sources") < names.index("token") < names.index("citation")
    sources = dict(events)["sources"]["sources"]
    assert sources and "Passwords" in sources[0]["section_path"]

    answer = "".join(d["text"] for n, d in events if n == "token")
    assert answer.startswith("According to the documents,") and "[1]" in answer
    citation = dict(events)["citation"]
    assert citation["index"] == 1 and citation["section_path"].endswith("Passwords")
    assert citation["snippet"] in sources[0]["snippet"] or citation["snippet"]
    assert citation["url"] == f"/documents/{citation['document_id']}?chunk={citation['chunk_id']}"
    done = dict(events)["done"]
    assert done["grounded"] is True and done["citations_rejected"] == 0

    # The model only ever saw retrieved chunks, as citable documents.
    assert llm.calls[0]["question"] == "How often must passwords be rotated?"
    assert len(llm.calls[0]["sources"]) == len(sources)

    detail = (await admin.client.get(f"/api/v1/chat/conversations/{conv}", headers=admin.headers)).json()
    assert detail["title"] == "How often must passwords be rotated?"
    user_msg, assistant = detail["messages"]
    assert user_msg["role"] == "user" and assistant["status"] == "complete" and assistant["grounded"]
    assert assistant["content"] == answer.strip()
    assert assistant["citations"][0]["index"] == 1
    async with resources.sessionmaker() as s:
        log = (await s.execute(select(RetrievalLog).where(RetrievalLog.source == "chat"))).scalar_one()
        assert log.message_id == uuid.UUID(assistant["id"])


async def test_follow_up_is_rewritten_into_a_standalone_query(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources, rag: Any, llm: ScriptedLLM
) -> None:
    admin = await register(client)
    await _ingest(admin, pipeline, resources, "policy.docx", build_docx())
    conv = await _new_conversation(admin)
    await _ask(admin, conv, "What does the policy say about passwords?")
    await _ask(admin, conv, "And how often?")
    routed = [c for c in llm.json_calls if c["name"] == "route_message"]
    assert len(routed) == 2 and "<conversation>" not in routed[0]["prompt"] and "<conversation>" in routed[1]["prompt"]
    assert [t.role for t in llm.calls[1]["history"]] == ["user", "assistant"]
    async with resources.sessionmaker() as s:
        last = (
            (await s.execute(select(Message).where(Message.role == "assistant").order_by(Message.created_at.desc())))
            .scalars()
            .first()
        )
    assert (
        last is not None and last.search_query == "And how often? (context: What does the policy say about passwords?)"
    )
    assert last.route == "documents"


# --- conversational replies (no documents needed) --------------------------------------------


async def _assistant_messages(resources: AppResources) -> list[Message]:
    async with resources.sessionmaker() as s:
        rows = await s.execute(select(Message).where(Message.role == "assistant").order_by(Message.created_at))
        return list(rows.scalars())


async def test_greeting_gets_a_conversational_reply_without_search(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources, rag: Any, llm: ScriptedLLM
) -> None:
    admin = await register(client)
    await _ingest(admin, pipeline, resources, "policy.docx", build_docx())
    conv = await _new_conversation(admin)

    events = await _ask(admin, conv, "Hello!")
    names = [n for n, _ in events]
    stages = [d["stage"] for n, d in events if n == "status"]
    assert names[0] == "meta" and names[-1] == "done"
    assert stages == ["thinking", "generating"]  # never "retrieving": no document search happened
    assert "sources" not in names and "citation" not in names
    assert "".join(d["text"] for n, d in events if n == "token").strip() == llm.chat_reply
    done = dict(events)["done"]
    assert done["route"] == "chat" and done["grounded"] is False and done["citations"] == []

    # Greeting rule: no router call, no retrieval, no grounded answer.
    assert llm.json_calls == [] and llm.calls == [] and len(llm.chat_calls) == 1
    async with resources.sessionmaker() as s:
        assert (await s.execute(select(RetrievalLog))).first() is None
    (assistant,) = await _assistant_messages(resources)
    assert assistant.route == "chat" and assistant.status == "complete" and assistant.search_query is None
    assert assistant.content == llm.chat_reply and assistant.model == "test/scripted-llm"

    detail = (await admin.client.get(f"/api/v1/chat/conversations/{conv}", headers=admin.headers)).json()
    assert detail["messages"][1]["route"] == "chat"


async def test_model_routed_chat_question_and_history(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources, rag: Any, llm: ScriptedLLM
) -> None:
    admin = await register(client)
    await _ingest(admin, pipeline, resources, "policy.docx", build_docx())
    conv = await _new_conversation(admin)
    await _ask(admin, conv, "How often must passwords be rotated?")  # a document answer first

    llm.json_responses["route_message"] = {"route": "chat", "search_query": ""}
    llm.chat_reply = "I can search your documents, summarise them and compare them."
    events = await _ask(admin, conv, "What can you do?")
    assert dict(events)["done"]["route"] == "chat"
    assert "sources" not in dict(events)
    # The chat reply sees the conversation so far, so it can refer back to it.
    assert [t.role for t in llm.chat_calls[0]["history"]] == ["user", "assistant"]
    assert len(llm.calls) == 1  # only the first question used the grounded path
    assert [m.route for m in await _assistant_messages(resources)] == ["documents", "chat"]
    async with resources.sessionmaker() as s:
        assert len((await s.execute(select(RetrievalLog))).all()) == 1  # the document question only


async def test_thanks_after_an_answer_is_a_chat_reply(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources, rag: Any, llm: ScriptedLLM
) -> None:
    admin = await register(client)
    await _ingest(admin, pipeline, resources, "policy.docx", build_docx())
    conv = await _new_conversation(admin)
    await _ask(admin, conv, "How often must passwords be rotated?")
    llm.chat_reply = "You're welcome! Let me know if you'd like to explore anything else."
    events = await _ask(admin, conv, "Thanks!")
    answer = "".join(d["text"] for n, d in events if n == "token")
    assert answer.strip() == llm.chat_reply and dict(events)["done"]["route"] == "chat"
    assert len(llm.calls) == 1 and len(llm.chat_calls) == 1


async def test_routing_failure_still_searches_the_documents(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources, rag: Any, llm: ScriptedLLM
) -> None:
    admin = await register(client)
    await _ingest(admin, pipeline, resources, "policy.docx", build_docx())
    conv = await _new_conversation(admin)

    def boom(_: str) -> dict[str, Any]:
        raise GenerationError("llm_unavailable", "down", retryable=True)

    llm.json_responses["route_message"] = boom
    events = await _ask(admin, conv, "How often must passwords be rotated?")
    done = dict(events)["done"]
    assert done["route"] == "documents" and done["grounded"] is True
    assert llm.chat_calls == [] and llm.calls[0]["question"] == "How often must passwords be rotated?"


async def test_chat_reply_failure_is_reported_and_recorded(
    client: httpx.AsyncClient, resources: AppResources, rag: Any, llm: ScriptedLLM
) -> None:
    admin = await register(client)
    conv = await _new_conversation(admin)
    llm.fail_with = GenerationError("llm_unavailable", "The language model is temporarily unavailable", retryable=True)
    events = await _ask(admin, conv, "Hello!")
    names = [n for n, _ in events]
    assert "error" in names  # the stream still ends with "done"; the client keeps the error state
    assert dict(events)["error"]["code"] == "llm_unavailable" and dict(events)["error"]["retryable"] is True
    (assistant,) = await _assistant_messages(resources)
    assert assistant.status == "error" and assistant.error == "llm_unavailable" and assistant.content == "Partial"
    assert assistant.route == "chat"


async def test_no_relevant_context_means_no_llm_call(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources, rag: Any, llm: ScriptedLLM
) -> None:
    admin = await register(client)
    await _ingest(admin, pipeline, resources, "policy.docx", build_docx())
    conv = await _new_conversation(admin)
    events = await _ask(admin, conv, "Quantum chromodynamics lattice gauge theory")
    answer = "".join(d["text"] for n, d in events if n == "token")
    assert "couldn't find information" in answer
    assert dict(events)["sources"]["sources"] == [] and dict(events)["done"]["grounded"] is False
    assert llm.calls == []
    async with resources.sessionmaker() as s:
        log = (await s.execute(select(RetrievalLog))).scalar_one()
    assert log.no_answer is True


async def test_fabricated_citations_are_dropped(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources, rag: Any, llm: ScriptedLLM
) -> None:
    from app.rag.generation.base import BlockEnd, CitationDelta, GenerationDone, TextDelta

    llm.script = lambda sources: [
        TextDelta("Passwords rotate every 30 days"),
        CitationDelta(0, "Passwords must be rotated every 30 days."),  # not what the source says
        CitationDelta(len(sources) + 3, "whatever"),  # a source that doesn't exist
        BlockEnd(),
        GenerationDone(model="m", stop_reason="end_turn"),
    ]
    admin = await register(client)
    await _ingest(admin, pipeline, resources, "policy.docx", build_docx())
    conv = await _new_conversation(admin)
    events = await _ask(admin, conv, "How often must passwords be rotated?")
    done = dict(events)["done"]
    assert done["grounded"] is False and done["citations"] == [] and done["citations_rejected"] == 2
    assert "citation" not in [n for n, _ in events]
    async with resources.sessionmaker() as s:
        assert (await s.execute(select(MessageCitation))).first() is None


async def test_llm_failure_is_reported_and_recorded(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources, rag: Any, llm: ScriptedLLM
) -> None:
    llm.fail_with = GenerationError("llm_unavailable", "The language model is temporarily unavailable", retryable=True)
    admin = await register(client)
    await _ingest(admin, pipeline, resources, "policy.docx", build_docx())
    conv = await _new_conversation(admin)
    events = await _ask(admin, conv, "How often must passwords be rotated?")
    error = dict(events)["error"]
    assert error == {
        "code": "llm_unavailable",
        "message": "The language model is temporarily unavailable",
        "retryable": True,
    }
    detail = (await admin.client.get(f"/api/v1/chat/conversations/{conv}", headers=admin.headers)).json()
    assert detail["messages"][1]["status"] == "error" and detail["messages"][1]["content"] == "Partial"


async def test_json_mode_returns_complete_answer(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources, rag: Any
) -> None:
    admin = await register(client)
    await _ingest(admin, pipeline, resources, "policy.docx", build_docx())
    conv = await _new_conversation(admin)
    resp = await client.post(
        f"/api/v1/chat/conversations/{conv}/messages",
        headers=admin.headers,
        json={"content": "How often must passwords be rotated?"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["message"]["grounded"] and body["message"]["citations"][0]["index"] == 1
    assert "[1]" in body["message"]["content"] and body["sources"]


async def test_conversations_are_private_to_their_owner(
    client: httpx.AsyncClient,
    make_client: ClientFactory,
    pipeline: IngestionPipeline,
    resources: AppResources,
    rag: Any,
) -> None:
    admin = await register(client)
    alice = await add_member(admin, make_client())
    conv = await _new_conversation(alice, title="Alice's research")
    for method, path, body in [
        ("GET", f"/api/v1/chat/conversations/{conv}", None),
        ("PATCH", f"/api/v1/chat/conversations/{conv}", {"title": "pwned"}),
        ("DELETE", f"/api/v1/chat/conversations/{conv}", None),
        ("POST", f"/api/v1/chat/conversations/{conv}/messages", {"content": "hi"}),
    ]:
        resp = await admin.client.request(method, path, headers=admin.headers, json=body)
        assert resp.status_code == 404, (method, path)  # even an admin can't read others' chats
    listed = (await admin.client.get("/api/v1/chat/conversations", headers=admin.headers)).json()
    assert listed["total"] == 0

    renamed = await alice.client.patch(
        f"/api/v1/chat/conversations/{conv}", headers=alice.headers, json={"title": "Renamed"}
    )
    assert renamed.json()["title"] == "Renamed"
    assert (await alice.client.delete(f"/api/v1/chat/conversations/{conv}", headers=alice.headers)).status_code == 204
    assert (await alice.client.get(f"/api/v1/chat/conversations/{conv}", headers=alice.headers)).status_code == 404


async def test_chat_scope_restricts_retrieval(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources, rag: Any, llm: ScriptedLLM
) -> None:
    admin = await register(client)
    policy = await _ingest(admin, pipeline, resources, "policy.docx", build_docx())
    await _ingest(admin, pipeline, resources, "other.pdf", build_pdf(["Passwords for the wifi are on the wall. " * 5]))
    conv = await _new_conversation(admin, scope={"document_ids": [policy["id"]]})
    events = await _ask(admin, conv, "passwords")
    assert {s["document_id"] for s in dict(events)["sources"]["sources"]} == {policy["id"]}


async def test_chat_without_llm_configured_returns_503(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources, rag: Any
) -> None:
    rag.llm = None
    rag.models = ModelRegistry(resources.settings)  # no provider key configured: no model is available
    admin = await register(client)
    conv = await _new_conversation(admin)
    resp = await client.post(
        f"/api/v1/chat/conversations/{conv}/messages", headers=admin.headers, json={"content": "hello"}
    )
    assert resp.status_code == 503 and resp.json()["error"]["code"] == "llm_not_configured"


# --- model choice, token cost, edit & resubmit ----------------------------------------------------------

OTHER = "test/other-llm"


def _price_models(rag: Any, **prices: Price) -> None:
    """Make these (fake) model ids selectable, each with a price."""
    rag.models.catalog = Catalog(
        models=[
            ModelSpec(id=model_id, provider="openai", label=model_id.split("/")[-1], prices=[price])
            for model_id, price in prices.items()
        ]
    )


async def _messages(s: Session, conv: str) -> list[dict[str, Any]]:
    detail = (await s.client.get(f"/api/v1/chat/conversations/{conv}", headers=s.headers)).json()
    return list(detail["messages"])


async def _post_raw(s: Session, conv: str, **body: Any) -> httpx.Response:
    return await s.client.post(
        f"/api/v1/chat/conversations/{conv}/messages",
        headers={**s.headers, "Accept": "text/event-stream"},
        json=body,
    )


async def test_models_endpoint_lists_selectable_models_with_prices(
    client: httpx.AsyncClient, rag: Any, llm: ScriptedLLM
) -> None:
    admin = await register(client)
    rag.models.register(ScriptedLLM(OTHER))
    _price_models(rag, **{llm.model_id: Price(input=1, output=2, cached_input=0.1), OTHER: Price(input=5, output=25)})
    body = (await client.get("/api/v1/chat/models", headers=admin.headers)).json()
    assert body["default"] == llm.model_id
    by_id = {m["id"]: m for m in body["models"]}
    assert set(by_id) == {llm.model_id, OTHER}
    assert by_id[llm.model_id]["is_default"] is True and by_id[OTHER]["is_default"] is False
    assert by_id[llm.model_id]["price"] == {"input": 1.0, "output": 2.0, "cached_input": 0.1}
    assert by_id[OTHER]["price"]["output"] == 25.0 and by_id[OTHER]["label"] == "other-llm"
    assert (await client.get("/api/v1/chat/models")).status_code == 401


async def test_the_chosen_model_answers_and_the_cost_is_recorded(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources, rag: Any, llm: ScriptedLLM
) -> None:
    admin = await register(client)
    await _ingest(admin, pipeline, resources, "policy.docx", build_docx())
    other = ScriptedLLM(OTHER)
    rag.models.register(other)
    # Routing runs on the default model (1 / 2 per M tokens); the answer on the chosen one (2 / 10 per M).
    _price_models(rag, **{llm.model_id: Price(input=1, output=2), OTHER: Price(input=2, output=10)})
    conv = await _new_conversation(admin)

    events = await _ask(admin, conv, "How often must passwords be rotated?", model=OTHER)
    done = dict(events)["done"]
    assert dict(events)["meta"]["model"] == OTHER and done["model"] == OTHER
    assert len(other.calls) == 1 and llm.calls == []  # the answer came from the chosen model only
    assert [c["name"] for c in llm.json_calls] == ["route_message"]  # routing stayed on the default

    usage = done["usage"]
    assert usage["input_tokens"] == 1000 + 100 and usage["output_tokens"] == 200 + 10
    assert usage["routing"]["model"] == llm.model_id and usage["answer"]["model"] == OTHER
    assert usage["routing"]["cost_usd"] == pytest.approx((100 * 1 + 10 * 2) / 1e6)
    assert usage["answer"]["cost_usd"] == pytest.approx((1000 * 2 + 200 * 10) / 1e6)
    assert usage["cost_usd"] == pytest.approx(usage["routing"]["cost_usd"] + usage["answer"]["cost_usd"])

    user_msg, assistant = await _messages(admin, conv)
    assert assistant["model"] == OTHER and assistant["usage"] == usage  # persisted, not recomputed
    assert assistant["latency_ms"] is not None and user_msg["usage"] == {}

    # A later price change does not rewrite history.
    _price_models(rag, **{llm.model_id: Price(input=100, output=100), OTHER: Price(input=100, output=100)})
    assert (await _messages(admin, conv))[1]["usage"]["cost_usd"] == pytest.approx(usage["cost_usd"])


async def test_a_greeting_costs_one_call_and_has_no_routing_step(
    client: httpx.AsyncClient, rag: Any, llm: ScriptedLLM
) -> None:
    admin = await register(client)
    _price_models(rag, **{llm.model_id: Price(input=1, output=2)})
    conv = await _new_conversation(admin)
    usage = dict(await _ask(admin, conv, "Hello!"))["done"]["usage"]
    assert "routing" not in usage and usage["answer"]["model"] == llm.model_id
    assert usage["input_tokens"] == 300 and usage["output_tokens"] == 50
    assert usage["cost_usd"] == pytest.approx((300 * 1 + 50 * 2) / 1e6)


async def test_a_model_without_a_price_reports_tokens_but_no_cost(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources, rag: Any, llm: ScriptedLLM
) -> None:
    admin = await register(client)
    await _ingest(admin, pipeline, resources, "policy.docx", build_docx())
    conv = await _new_conversation(admin)
    usage = dict(await _ask(admin, conv, "How often must passwords be rotated?"))["done"]["usage"]
    assert usage["cost_usd"] is None and usage["input_tokens"] == 1100 and usage["output_tokens"] == 210


async def test_an_unknown_model_is_rejected_before_anything_is_stored(
    client: httpx.AsyncClient, rag: Any, llm: ScriptedLLM
) -> None:
    admin = await register(client)
    conv = await _new_conversation(admin)
    resp = await _post_raw(admin, conv, content="Hello!", model="not-a-model")
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "unknown_model"
    assert await _messages(admin, conv) == [] and llm.chat_calls == []


async def test_edit_and_resubmit_replaces_the_message_and_everything_after_it(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources, rag: Any, llm: ScriptedLLM
) -> None:
    admin = await register(client)
    await _ingest(admin, pipeline, resources, "policy.docx", build_docx())
    conv = await _new_conversation(admin)
    first = "How often must passwords be rotated?"
    await _ask(admin, conv, first)
    await _ask(admin, conv, "Which rules apply to passwords?")
    await _ask(admin, conv, "And how often?")
    before = await _messages(admin, conv)
    assert [m["role"] for m in before] == ["user", "assistant"] * 3
    edited = before[2]

    events = await _ask(admin, conv, "Must passwords be rotated?", edit_message_id=edited["id"])
    assert dict(events)["done"]["grounded"] is True

    after = await _messages(admin, conv)
    assert [m["content"] for m in after if m["role"] == "user"] == [first, "Must passwords be rotated?"]
    assert [m["role"] for m in after] == ["user", "assistant", "user", "assistant"]
    assert after[2]["edit_of"] == edited["id"] and after[0]["edit_of"] is None
    assert after[0]["id"] == before[0]["id"] and after[1]["id"] == before[1]["id"]  # earlier turns are untouched
    # The new answer was generated with only the turns before the edited message as context.
    history = llm.calls[-1]["history"]
    assert [t.role for t in history] == ["user", "assistant"] and history[0].content == first

    # Nothing was deleted: the replaced messages are kept, marked superseded.
    async with resources.sessionmaker() as s:
        rows = (await s.execute(select(Message).where(Message.conversation_id == uuid.UUID(conv)))).scalars().all()
    assert len(rows) == 8
    assert {r.content for r in rows if r.superseded_at is not None} >= {
        "Which rules apply to passwords?",
        "And how often?",
    }
    assert sum(r.superseded_at is not None for r in rows) == 4  # the edited turn and the one after it

    # The conversation continues normally from the edited point.
    await _ask(admin, conv, "How often must passwords be rotated?")
    assert len(await _messages(admin, conv)) == 6


async def test_editing_the_first_message_renames_the_conversation(
    client: httpx.AsyncClient, rag: Any, llm: ScriptedLLM
) -> None:
    admin = await register(client)
    conv = await _new_conversation(admin)
    await _ask(admin, conv, "Hello!")
    await _ask(admin, conv, "Thanks!")
    first = (await _messages(admin, conv))[0]
    title = (await admin.client.get(f"/api/v1/chat/conversations/{conv}", headers=admin.headers)).json()["title"]
    assert title == "Hello!"
    await _ask(admin, conv, "Good morning", edit_message_id=first["id"])
    detail = (await admin.client.get(f"/api/v1/chat/conversations/{conv}", headers=admin.headers)).json()
    assert detail["title"] == "Good morning" and [m["role"] for m in detail["messages"]] == ["user", "assistant"]


async def test_an_edit_can_use_a_different_model(client: httpx.AsyncClient, rag: Any, llm: ScriptedLLM) -> None:
    admin = await register(client)
    other = ScriptedLLM(OTHER)
    rag.models.register(other)
    _price_models(rag, **{llm.model_id: Price(input=1, output=1), OTHER: Price(input=1, output=1)})
    conv = await _new_conversation(admin)
    await _ask(admin, conv, "Hello!")
    first = (await _messages(admin, conv))[0]
    await _ask(admin, conv, "Good morning", edit_message_id=first["id"], model=OTHER)
    assert [m["model"] for m in await _messages(admin, conv) if m["role"] == "assistant"] == [OTHER]
    assert len(other.chat_calls) == 1 and len(llm.chat_calls) == 1  # one reply from each model


async def test_only_your_own_live_user_messages_can_be_edited(
    client: httpx.AsyncClient, make_client: ClientFactory, rag: Any, llm: ScriptedLLM
) -> None:
    admin = await register(client)
    conv = await _new_conversation(admin)
    await _ask(admin, conv, "Hello!")
    user_msg, assistant = await _messages(admin, conv)

    async def edit(target: str, conversation: str = conv, session: Session = admin) -> httpx.Response:
        return await _post_raw(session, conversation, content="Hi!", edit_message_id=target)

    for target in (assistant["id"], str(uuid.uuid4())):  # an assistant message / a message that doesn't exist
        resp = await edit(target)
        assert resp.status_code == 404 and resp.json()["error"]["code"] == "message_not_found"

    other_conv = await _new_conversation(admin)
    assert (await edit(user_msg["id"], other_conv)).status_code == 404  # a message from another conversation

    stranger = await register(make_client())
    assert (await edit(user_msg["id"], conv, stranger)).status_code == 404  # someone else's conversation

    await _ask(admin, conv, "Hi!", edit_message_id=user_msg["id"])
    assert (await edit(user_msg["id"])).status_code == 404  # already replaced: no longer part of the conversation
    assert len(await _messages(admin, conv)) == 2


async def test_a_model_that_hits_its_quota_is_flagged_then_recovers(
    client: httpx.AsyncClient, rag: Any, llm: ScriptedLLM
) -> None:
    admin = await register(client)
    other = ScriptedLLM(OTHER)
    rag.models.register(other)
    _price_models(rag, **{llm.model_id: Price(input=1, output=1), OTHER: Price(input=1, output=1)})
    conv = await _new_conversation(admin)

    async def flags() -> dict[str, str | None]:
        body = (await client.get("/api/v1/chat/models", headers=admin.headers)).json()
        return {m["id"]: m["unavailable_reason"] for m in body["models"]}

    assert await flags() == {llm.model_id: None, OTHER: None}
    reason = "You have reached your specified API usage limits. You will regain access on 2026-12-01."
    other.fail_with = GenerationError("llm_quota_exceeded", reason)
    events = await _ask(admin, conv, "Hello!", model=OTHER)
    assert dict(events)["error"] == {"code": "llm_quota_exceeded", "message": reason, "retryable": False}
    assert await flags() == {llm.model_id: None, OTHER: reason}  # greyed out, with the provider's explanation

    other.fail_with = None  # the limit was lifted: the next call works and clears the flag
    assert "error" not in dict(await _ask(admin, conv, "Thanks!", model=OTHER))
    assert await flags() == {llm.model_id: None, OTHER: None}


async def test_chat_model_allowlist_filters_the_list_and_rejects_other_models(
    client: httpx.AsyncClient, rag: Any, llm: ScriptedLLM, resources: AppResources
) -> None:
    admin = await register(client)
    allowed = ScriptedLLM("test/allowed-llm")
    blocked = ScriptedLLM("test/blocked-llm")
    for provider in (allowed, blocked):
        rag.models.register(provider)
    _price_models(
        rag,
        **{
            llm.model_id: Price(input=1, output=1),
            allowed.model_id: Price(input=1, output=1),
            blocked.model_id: Price(input=9, output=9),
        },
    )
    resources.settings.chat_model_ids = f"{llm.model_id},{allowed.model_id}"

    body = (await client.get("/api/v1/chat/models", headers=admin.headers)).json()
    assert {m["id"] for m in body["models"]} == {llm.model_id, allowed.model_id}

    conv = await _new_conversation(admin)
    assert dict(await _ask(admin, conv, "Hello!", model=allowed.model_id))["done"]["model"] == allowed.model_id
    refused = await _post_raw(admin, conv, content="Hello!", model=blocked.model_id)
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "unknown_model"
    assert blocked.chat_calls == []  # the model that isn't on the list is never called
