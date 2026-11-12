"""Hyperlinks end to end: extraction during ingestion, PostgreSQL records, the API, access rules,
reprocessing and deletion, and link questions in chat (real PostgreSQL + Qdrant, scripted LLM)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from app.core.resources import AppResources
from app.models import DocumentLink
from app.rag.generation.base import BlockEnd, CitationDelta, GenerationDone, SourceDocument, TextDelta
from app.rag.ingestion.pipeline import IngestionPipeline
from app.rag.runtime import build_rag_runtime
from app.workers.tasks import purge_document
from tests.documents import (
    GITHUB,
    LINKEDIN,
    PORTFOLIO,
    REPO,
    build_docx,
    build_docx_with_links,
    build_pdf_with_links,
    build_xlsx_with_links,
)
from tests.fakes import ANSWER_USAGE, HashSparse, LexicalReranker, ScriptedLLM
from tests.integration.api_helpers import Session, add_member, register, upload_one
from tests.integration.rag_helpers import run_ingestion
from tests.integration.test_retrieval_chat import _ask, _new_conversation

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


async def _links(s: Session, document_id: str) -> dict[str, Any]:
    resp = await s.client.get(f"/api/v1/documents/{document_id}/links", headers=s.headers)
    assert resp.status_code == 200, resp.text
    return dict(resp.json())


async def _search_links(s: Session, query: str, **body: Any) -> list[dict[str, Any]]:
    resp = await s.client.post("/api/v1/search/links", headers=s.headers, json={"query": query, **body})
    assert resp.status_code == 200, resp.text
    return list(resp.json()["results"])


async def _link_rows(resources: AppResources, document_id: str) -> int:
    async with resources.sessionmaker() as s:
        return int(
            await s.scalar(
                select(func.count()).select_from(DocumentLink).where(DocumentLink.document_id == uuid.UUID(document_id))
            )
            or 0
        )


def _quote_link_line(url: str) -> Callable[[list[SourceDocument]], list[Any]]:
    """An answer that quotes the link-list line containing ``url`` and cites it."""

    def script(sources: list[SourceDocument]) -> list[Any]:
        for i, source in enumerate(sources):
            for passage in source.passages:
                if url in passage:
                    return [
                        TextDelta(f"I found it: {url}"),
                        CitationDelta(source_index=i, cited_text=passage, start_passage=0, end_passage=1),
                        BlockEnd(),
                        GenerationDone(model="test/scripted-llm", stop_reason="end_turn", usage=ANSWER_USAGE),
                    ]
        return [TextDelta("Not found."), GenerationDone(model="t", stop_reason="end_turn", usage=ANSWER_USAGE)]

    return script


# --- extraction, storage, API -----------------------------------------------------------------------


async def test_ingestion_stores_links_from_every_format(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources
) -> None:
    admin = await register(client)
    pdf = await _ingest(admin, pipeline, resources, "Jane Doe Resume.pdf", build_pdf_with_links())
    docx = await _ingest(admin, pipeline, resources, "Jane CV.docx", build_docx_with_links())
    xlsx = await _ingest(admin, pipeline, resources, "People.xlsx", build_xlsx_with_links())

    body = await _links(admin, pdf["id"])
    items = {i["url"]: i for i in body["items"]}
    assert body["total"] == len(body["items"]) == 5
    assert items[GITHUB]["anchor_text"] == "GitHub Profile" and items[GITHUB]["link_type"] == "hyperlink"
    assert items[GITHUB]["page_number"] == 1 and items[GITHUB]["location"] == "page 1"
    assert items[GITHUB]["domain"] == "github.com" and items[GITHUB]["document_title"] == "Jane Doe Resume"
    assert items["https://talks.example.org/jane"]["link_type"] == "url"
    assert items[REPO]["page_number"] == items[PORTFOLIO]["page_number"] == 2

    docx_items = {i["url"]: i for i in (await _links(admin, docx["id"]))["items"]}
    assert docx_items[LINKEDIN]["section_name"] == "Jane Doe > Contact"
    xlsx_items = {i["url"]: i for i in (await _links(admin, xlsx["id"]))["items"]}
    assert xlsx_items[GITHUB]["location"] == "sheet People, cell B2"

    # The hidden targets are part of the indexed text, so ordinary search finds them too.
    chunks = (await client.get(f"/api/v1/documents/{pdf['id']}/chunks", headers=admin.headers)).json()["items"]
    assert any(f"GitHub Profile: {GITHUB}" in c["text"] for c in chunks)
    detail = (await client.get(f"/api/v1/documents/{pdf['id']}", headers=admin.headers)).json()
    assert detail["metadata"]["link_count"] == 5


async def test_link_search_is_exact_and_respects_access(
    client: httpx.AsyncClient, make_client: ClientFactory, pipeline: IngestionPipeline, resources: AppResources
) -> None:
    admin = await register(client)
    alice = await add_member(admin, make_client())
    outsider = await register(make_client(), org="Other Corp")
    shared = await _ingest(admin, pipeline, resources, "Jane Doe Resume.pdf", build_pdf_with_links())
    secret = await _ingest(admin, pipeline, resources, "Secret.docx", build_docx_with_links(), visibility="private")

    github = await _search_links(admin, "What is the GitHub link?")
    assert {(h["url"], h["document_title"]) for h in github} == {
        (GITHUB, "Jane Doe Resume"),
        (REPO, "Jane Doe Resume"),
        (GITHUB, "Secret"),
        (REPO, "Secret"),
    }
    assert {h["url"] for h in await _search_links(admin, "linkedin")} == {LINKEDIN}
    assert {h["url"] for h in await _search_links(admin, '"Personal Portfolio"')} == {PORTFOLIO}
    scoped = await _search_links(admin, "all links", filters={"document_ids": [shared["id"]]})
    assert {h["document_id"] for h in scoped} == {shared["id"]}

    # Alice cannot see the private document's links, by search or directly; another org sees nothing.
    assert {h["document_title"] for h in await _search_links(alice, "github")} == {"Jane Doe Resume"}
    assert (await alice.client.get(f"/api/v1/documents/{secret['id']}/links", headers=alice.headers)).status_code == 404
    assert await _search_links(outsider, "all links") == []


async def test_reprocessing_replaces_links_and_deletion_removes_them(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources
) -> None:
    admin = await register(client)
    doc = await _ingest(admin, pipeline, resources, "Jane Doe Resume.pdf", build_pdf_with_links())
    assert await _link_rows(resources, doc["id"]) == 5

    resp = await client.post(f"/api/v1/documents/{doc['id']}/reprocess", headers=admin.headers)
    assert resp.status_code == 202, resp.text
    assert await run_ingestion(pipeline, resources, doc["id"]) == "indexed"
    assert await _link_rows(resources, doc["id"]) == 5  # replaced, not duplicated
    body = await _links(admin, doc["id"])
    assert body["index_version"] == 2 and body["total"] == 5

    assert (await client.delete(f"/api/v1/documents/{doc['id']}", headers=admin.headers)).status_code == 202
    assert await _search_links(admin, "all links") == []  # gone from lookups at once

    class Store:
        async def delete_document_everywhere(self, document_id: uuid.UUID) -> None:
            pass

    ctx = {"sessionmaker": resources.sessionmaker, "storage": resources.storage, "store": Store()}
    assert await purge_document(ctx, doc["id"]) == "purged"
    assert await _link_rows(resources, doc["id"]) == 0  # and from the database on purge


# --- chat ---------------------------------------------------------------------------------------------


async def test_chat_answers_link_questions_with_the_exact_url(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources, rag: Any, llm: ScriptedLLM
) -> None:
    admin = await register(client)
    await _ingest(admin, pipeline, resources, "Jane Doe Resume.pdf", build_pdf_with_links())
    await _ingest(admin, pipeline, resources, "policy.docx", build_docx())
    conv = await _new_conversation(admin)

    llm.script = _quote_link_line(GITHUB)
    events = await _ask(admin, conv, "What is the GitHub link in my resume?")
    done = dict(events)["done"]
    answer = "".join(d["text"] for n, d in events if n == "token")
    assert GITHUB in answer and done["grounded"] and done["unverified_urls"] == 0
    citation = dict(events)["citation"]
    assert citation["document_title"] == "Jane Doe Resume" and GITHUB in citation["snippet"]

    # The model got the matched links first, as one citable list per document, with locations.
    first = llm.calls[0]["sources"][0]
    assert first.title.startswith("Jane Doe Resume")
    assert any(f"GitHub Profile -> {GITHUB} (page 1, hyperlink)" == p.removeprefix("- ") for p in first.passages)
    assert not any(LINKEDIN in p for p in first.passages)  # exact domain match only

    llm.script = _quote_link_line(LINKEDIN)
    events = await _ask(admin, conv, "Find the LinkedIn URL.")
    assert LINKEDIN in "".join(d["text"] for n, d in events if n == "token")


async def test_chat_lists_links_across_documents_and_routes_link_questions_to_documents(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources, rag: Any, llm: ScriptedLLM
) -> None:
    admin = await register(client)
    await _ingest(admin, pipeline, resources, "Jane Doe Resume.pdf", build_pdf_with_links())
    await _ingest(admin, pipeline, resources, "People.xlsx", build_xlsx_with_links())
    conv = await _new_conversation(admin)

    # Even if the router thinks this is small talk, matching links send it to the documents.
    llm.json_responses["route_message"] = {"route": "chat", "search_query": "Which websites are in my documents?"}
    llm.script = _quote_link_line(PORTFOLIO)
    events = await _ask(admin, conv, "Which websites are referenced in my uploaded documents?")
    assert dict(events)["done"]["route"] == "documents"
    link_sources = [s for s in llm.calls[0]["sources"] if s.passages[0].startswith("Hyperlinks extracted")]
    assert {s.title.split(" — ")[0] for s in link_sources} == {"Jane Doe Resume", "People"}
    listed = "\n".join(p for s in link_sources for p in s.passages)
    assert "(sheet People, cell B2, hyperlink)" in listed and "(page 2, hyperlink)" in listed


async def test_chat_never_shows_links_from_documents_the_user_cannot_read(
    client: httpx.AsyncClient,
    make_client: ClientFactory,
    pipeline: IngestionPipeline,
    resources: AppResources,
    rag: Any,
    llm: ScriptedLLM,
) -> None:
    admin = await register(client)
    alice = await add_member(admin, make_client())
    await _ingest(admin, pipeline, resources, "Secret.docx", build_docx_with_links(), visibility="private")
    conv = await _new_conversation(alice)

    events = await _ask(alice, conv, "What is the GitHub URL?")
    assert dict(events)["sources"]["sources"] == []
    answer = "".join(d["text"] for n, d in events if n == "token")
    assert "couldn't find a link" in answer and GITHUB not in answer
    assert llm.calls == []  # nothing to answer from: no model call


async def test_invented_urls_are_counted(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources, rag: Any, llm: ScriptedLLM
) -> None:
    admin = await register(client)
    await _ingest(admin, pipeline, resources, "Jane Doe Resume.pdf", build_pdf_with_links())
    conv = await _new_conversation(admin)
    llm.script = lambda sources: [
        TextDelta("Try https://github.com/someone-else"),
        GenerationDone(model="test/scripted-llm", stop_reason="end_turn", usage=ANSWER_USAGE),
    ]
    events = await _ask(admin, conv, "What is the GitHub link?")
    assert dict(events)["done"]["unverified_urls"] == 1


async def test_general_questions_about_websites_stay_small_talk(
    client: httpx.AsyncClient, pipeline: IngestionPipeline, resources: AppResources, rag: Any, llm: ScriptedLLM
) -> None:
    admin = await register(client)
    await _ingest(admin, pipeline, resources, "Jane Doe Resume.pdf", build_pdf_with_links())
    conv = await _new_conversation(admin)

    llm.json_responses["route_message"] = {"route": "chat", "search_query": ""}
    for question in ("What's a good website for learning Python?", "Thanks for the link!"):
        events = await _ask(admin, conv, question)
        assert dict(events)["done"]["route"] == "chat", question
    assert llm.calls == []  # no grounded answer, no link list sent to the model
