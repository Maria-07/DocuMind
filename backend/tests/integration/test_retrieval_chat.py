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


async def test_follow_up_question_is_condensed_for_retrieval(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources, rag: Any, llm: ScriptedLLM
) -> None:
    admin = await register(client)
    await _ingest(admin, pipeline, resources, "policy.docx", build_docx())
    conv = await _new_conversation(admin)
    await _ask(admin, conv, "What does the policy say about passwords?")
    await _ask(admin, conv, "And how often?")
    assert llm.condense_calls == ["And how often?"]
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
    admin = await register(client)
    conv = await _new_conversation(admin)
    resp = await client.post(
        f"/api/v1/chat/conversations/{conv}/messages", headers=admin.headers, json={"content": "hello"}
    )
    assert resp.status_code == 503 and resp.json()["error"]["code"] == "llm_not_configured"
