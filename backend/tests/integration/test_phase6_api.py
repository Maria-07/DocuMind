"""Comparison, evaluation, analytics and embeddings APIs against real PostgreSQL + Qdrant (fake models)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.core.resources import AppResources
from app.rag.evaluation.runner import EvaluationRunner
from app.rag.ingestion.pipeline import IngestionPipeline
from tests.documents import build_docx, build_pdf
from tests.fakes import ScriptedLLM
from tests.integration.api_helpers import Session, add_member, register
from tests.integration.test_retrieval_chat import _ask, _ingest, _new_conversation, llm, rag  # noqa: F401

ClientFactory = Callable[[], httpx.AsyncClient]


def _comparison_response(prompt: str) -> dict[str, Any]:
    # Cite source 1 for D1 and the last source for D2 (sources are numbered across documents).
    last = prompt.count('<source id="')
    return {
        "aspects": [
            {
                "aspect": "Data retention",
                "conflict": True,
                "difference": "Different retention periods",
                "findings": [
                    {"document": "D1", "status": "present", "finding": "Seven years", "source_ids": [1]},
                    {"document": "D2", "status": "present", "finding": "Ten years", "source_ids": [last, 1]},
                ],
            }
        ],
        "missing_or_inconsistent": [],
        "summary": "The documents disagree on retention.",
    }


async def _two_docs(admin: Session, pipeline: IngestionPipeline, resources: AppResources) -> tuple[dict, dict]:
    a = await _ingest(admin, pipeline, resources, "policy.docx", build_docx())
    b = await _ingest(
        admin,
        pipeline,
        resources,
        "retention.pdf",
        build_pdf(["Customer records are retained for ten years. Logs for two years. " * 3]),
    )
    return a, b


async def test_compare_documents(
    client: httpx.AsyncClient,
    make_client: ClientFactory,
    pipeline: IngestionPipeline,
    resources: AppResources,
    rag: Any,
    llm: ScriptedLLM,
) -> None:
    admin = await register(client)
    a, b = await _two_docs(admin, pipeline, resources)
    llm.json_responses["document_comparison"] = _comparison_response

    resp = await client.post(
        "/api/v1/comparisons",
        headers=admin.headers,
        json={"document_ids": [a["id"], b["id"]], "question": "How long are records kept?"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "complete"
    aspect = body["result"]["aspects"][0]
    d1, d2 = aspect["findings"]
    assert d1["document_id"] == a["id"] and d1["citations"][0]["document_id"] == a["id"]
    # D2 cited source 1, which belongs to D1: dropped and counted.
    assert [c["document_id"] for c in d2["citations"]] == [b["id"]]
    assert body["result"]["citations_rejected"] == 1
    assert aspect["conflict"] is True
    # Each document contributed its own sources to the prompt.
    prompt = llm.json_calls[0]["prompt"]
    assert '<document label="D1" title="policy">' in prompt and '<document label="D2" title="retention">' in prompt

    # General comparison (no question) uses each document's opening content.
    resp = await client.post("/api/v1/comparisons", headers=admin.headers, json={"document_ids": [a["id"], b["id"]]})
    assert resp.status_code == 201 and resp.json()["question"] is None

    listed = (await client.get("/api/v1/comparisons", headers=admin.headers)).json()
    assert len(listed) == 2
    # Other users can't read it; documents they can't see can't be compared.
    other = await add_member(admin, make_client())
    assert (await other.client.get(f"/api/v1/comparisons/{body['id']}", headers=other.headers)).status_code == 404
    await client.patch(f"/api/v1/documents/{a['id']}", headers=admin.headers, json={"visibility": "private"})
    resp = await other.client.post(
        "/api/v1/comparisons", headers=other.headers, json={"document_ids": [a["id"], b["id"]]}
    )
    assert resp.status_code == 404

    bad = await client.post("/api/v1/comparisons", headers=admin.headers, json={"document_ids": [a["id"], a["id"]]})
    assert bad.status_code == 422


async def test_evaluation_dataset_run_and_metrics(
    client: httpx.AsyncClient,
    make_client: ClientFactory,
    pipeline: IngestionPipeline,
    resources: AppResources,
    rag: Any,
    llm: ScriptedLLM,
) -> None:
    admin = await register(client)
    policy, _ = await _two_docs(admin, pipeline, resources)

    ds = (
        await client.post(
            "/api/v1/evaluations/datasets", headers=admin.headers, json={"name": "Policies", "description": "smoke"}
        )
    ).json()
    cases = [
        {
            "question": "How often must passwords be rotated?",
            "expected_answer": "Every 90 days.",
            "relevant_document_ids": [policy["id"]],
            "evidence": [{"document_id": policy["id"], "quote": "rotated every 90 days"}],
        },
        {"question": "Quantum chromodynamics lattice gauge theory", "relevant_document_ids": []},
    ]
    resp = await client.post(f"/api/v1/evaluations/datasets/{ds['id']}/cases", headers=admin.headers, json=cases)
    assert resp.status_code == 201 and len(resp.json()) == 2

    bad = [{"question": "Unknown document?", "relevant_document_ids": [str(uuid.uuid4())]}]
    resp = await client.post(f"/api/v1/evaluations/datasets/{ds['id']}/cases", headers=admin.headers, json=bad)
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "unknown_documents"

    run = (await client.post("/api/v1/evaluations/run", headers=admin.headers, json={"dataset_id": ds["id"]})).json()
    assert run["status"] == "queued" and run["case_count"] == 2
    assert ("run_evaluation", (run["id"],), f"eval:{run['id']}") in resources.job_queue.jobs  # type: ignore[attr-defined]
    dup = await client.post("/api/v1/evaluations/run", headers=admin.headers, json={"dataset_id": ds["id"]})
    assert dup.status_code == 409

    # Execute the queued run the way the worker does.
    runner = EvaluationRunner(
        sessionmaker=resources.sessionmaker, settings=resources.settings, retriever=rag.retriever, llm=llm
    )
    finished = await runner.run(uuid.UUID(run["id"]))
    assert finished.status == "complete", finished.error

    detail = (await client.get(f"/api/v1/evaluations/results/{run['id']}", headers=admin.headers)).json()
    assert detail["completed_cases"] == 2 and detail["config"]["llm_model"] == resources.settings.llm_model
    by_q = {r["question"]: r for r in detail["results"]}
    answerable = by_q["How often must passwords be rotated?"]["metrics"]
    assert answerable["retrieval.recall@5"] == 1.0 and answerable["retrieval.mrr"] > 0
    assert answerable["retrieval.doc_recall@5"] == 1.0
    assert answerable["answer.citation_precision"] == 1.0  # the scripted answer cites the evidence chunk
    assert answerable["answer.faithfulness"] == 1.0 and answerable["answer.correctness"] == 0.75
    assert answerable["answer.abstention_accuracy"] is True
    unanswerable = by_q["Quantum chromodynamics lattice gauge theory"]
    assert unanswerable["answer"] is None and unanswerable["metrics"]["answer.abstention_accuracy"] is True
    assert detail["metrics"]["cases"] == 2 and detail["metrics"]["answer.abstention_accuracy"] == 1.0

    second = (await client.post("/api/v1/evaluations/run", headers=admin.headers, json={"dataset_id": ds["id"]})).json()
    assert second["baseline_run_id"] == run["id"]  # defaults to the last complete run
    await runner.run(uuid.UUID(second["id"]))
    history = (await client.get(f"/api/v1/evaluations/metrics?dataset_id={ds['id']}", headers=admin.headers)).json()
    assert [p["run_id"] for p in history] == [run["id"], second["id"]]
    datasets = (await client.get("/api/v1/evaluations/datasets", headers=admin.headers)).json()
    assert datasets[0]["case_count"] == 2 and datasets[0]["last_run"]["id"] == second["id"]

    # Managers can view, only admins can run; regular users can't see evaluations at all.
    manager = await add_member(admin, make_client(), role="manager")
    assert (await manager.client.get("/api/v1/evaluations/datasets", headers=manager.headers)).status_code == 200
    resp = await manager.client.post("/api/v1/evaluations/run", headers=manager.headers, json={"dataset_id": ds["id"]})
    assert resp.status_code == 403
    user = await add_member(admin, make_client())
    assert (await user.client.get("/api/v1/evaluations/datasets", headers=user.headers)).status_code == 403


async def test_analytics_and_embeddings(
    client: httpx.AsyncClient,
    make_client: ClientFactory,
    pipeline: IngestionPipeline,
    resources: AppResources,
    rag: Any,
) -> None:
    admin = await register(client)
    await _two_docs(admin, pipeline, resources)
    conv = await _new_conversation(admin)
    await _ask(admin, conv, "How often must passwords be rotated?")
    # A separate conversation: as a follow-up it would be condensed with the first question.
    await _ask(admin, await _new_conversation(admin), "Quantum chromodynamics lattice gauge theory")

    overview = (await client.get("/api/v1/analytics/overview", headers=admin.headers)).json()
    assert overview["documents_total"] == 2 and overview["documents_by_status"]["indexed"] == 2
    assert overview["chunks_total"] > 0 and overview["conversations_total"] == 2 and overview["questions_7d"] == 2
    assert overview["grounded_rate_7d"] == 0.5 and overview["storage_bytes"] > 0

    docs = (await client.get("/api/v1/analytics/documents?days=7", headers=admin.headers)).json()
    assert len(docs["days"]) == 7 and docs["uploads"][-1] == 2 and docs["indexed"][-1] == 2
    assert docs["by_type"] == {"docx": 1, "pdf": 1}
    assert docs["recent_activity"][0]["action"] == "document.uploaded"
    assert {a["title"] for a in docs["recent_activity"]} == {"policy", "retention"}

    retrieval = (await client.get("/api/v1/analytics/retrieval?days=7", headers=admin.headers)).json()
    assert retrieval["queries"][-1] == 2 and retrieval["no_answer_rate"] == 0.5
    assert retrieval["retrieval_latency_ms"]["p50"] is not None and retrieval["answers_total"] == 2

    emb = (await client.get("/api/v1/embeddings", headers=admin.headers)).json()
    active = next(c for c in emb["configs"] if c["is_active"])
    assert active["documents"] == 2 and active["points"] == active["chunks"] > 0
    assert emb["documents_stale"] == 0 and emb["documents_on_active"] == 2
    assert (await client.post("/api/v1/embeddings/reindex", headers=admin.headers, json={"mode": "stale"})).json() == {
        "queued": 0
    }
    assert (await client.post("/api/v1/embeddings/reindex", headers=admin.headers, json={"mode": "all"})).json() == {
        "queued": 2
    }

    user = await add_member(admin, make_client())
    for path in ("/api/v1/analytics/overview", "/api/v1/embeddings"):
        assert (await user.client.get(path, headers=user.headers)).status_code == 403

    # The member directory (share dialogs) is visible to every member, scoped to the organization.
    members = (await user.client.get("/api/v1/organization/members", headers=user.headers)).json()
    assert {m["id"] for m in members} == {admin.user["id"], user.user["id"]}
    assert set(members[0]) == {"id", "full_name", "email"}
    other = await register(make_client(), org="Other")
    other_members = (await other.client.get("/api/v1/organization/members", headers=other.headers)).json()
    assert [m["id"] for m in other_members] == [other.user["id"]]


@pytest.fixture
def _unused() -> None:  # keeps the imported fixtures referenced for linters
    return None
