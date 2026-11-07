from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.models.enums import FileType
from app.rag.evaluation.judge import judge_answer
from app.rag.evaluation.runner import aggregate, regressions
from app.rag.retrieval import RetrievedChunk
from app.services.comparison import comparison_schema, validate_comparison
from tests.fakes import ScriptedLLM


def doc(title: str) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), title=title, file_type=FileType.PDF)


def chunk(document_id: uuid.UUID, text: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        document_id=document_id,
        document_title="t",
        file_type="pdf",
        kind="text",
        text=text,
        token_count=5,
        index_version=1,
        page_start=2,
    )


def _walk(schema: dict) -> list[dict]:  # every object node in a JSON schema
    nodes = [schema] if schema.get("type") == "object" else []
    for v in schema.get("properties", {}).values():
        nodes += _walk(v)
    if "items" in schema:
        nodes += _walk(schema["items"])
    return nodes


def test_comparison_schema_is_strict_compatible() -> None:
    for node in _walk(comparison_schema()):
        assert node["additionalProperties"] is False
        assert set(node["required"]) == set(node["properties"])


def test_validate_comparison_maps_labels_and_rejects_cross_document_citations() -> None:
    a, b = doc("Policy 2025"), doc("Policy 2026")
    sources = {1: (0, chunk(a.id, "Retention: 7 years")), 2: (1, chunk(b.id, "Retention: 10 years"))}
    raw = {
        "aspects": [
            {
                "aspect": "Retention",
                "conflict": True,
                "difference": "7 vs 10 years",
                "findings": [
                    {"document": "D1", "status": "present", "finding": "7 years", "source_ids": [1, 2]},  # 2 is D2's
                    {"document": "d2", "status": "present", "finding": "10 years", "source_ids": [2]},
                ],
            },
            {
                "aspect": "Scope",
                "conflict": False,
                "difference": "",
                "findings": [
                    {"document": "D1", "status": "present", "finding": "claims without evidence", "source_ids": [99]},
                    {"document": "D7", "status": "present", "finding": "unknown doc", "source_ids": []},
                ],
            },
        ],
        "missing_or_inconsistent": ["D2 lacks a scope section"],
        "summary": "Retention increased.",
    }
    result, rejected = validate_comparison(raw, [a, b], sources)  # type: ignore[arg-type]
    retention, scope = result["aspects"]
    assert [f["document_id"] for f in retention["findings"]] == [str(a.id), str(b.id)]
    assert [c["source_id"] for c in retention["findings"][0]["citations"]] == [1]
    assert retention["findings"][1]["citations"][0]["url"].startswith(f"/documents/{b.id}?chunk=")
    # A "present" claim whose only citation was invalid is downgraded; a missing document is filled.
    assert scope["findings"][0]["status"] == "unclear"
    assert scope["findings"][1] == {
        "document_id": str(b.id),
        "status": "unclear",
        "finding": "Not assessed by the model.",
        "citations": [],
    }
    assert rejected == 2  # source 2 cited for D1, source 99 unknown
    assert result["documents"][0]["title"] == "Policy 2025"
    # Labels are shown to users as titles; unknown labels and other words are left alone.
    assert result["missing_or_inconsistent"] == ["“Policy 2026” lacks a scope section"]
    assert scope["findings"][0]["finding"] == "claims without evidence"


def test_aggregate_and_regressions() -> None:
    agg = aggregate(
        [
            {"retrieval.recall@5": 1.0, "answer.abstention_accuracy": True, "answer.hallucination_rate": 0.0},
            {"retrieval.recall@5": 0.5, "answer.abstention_accuracy": False, "answer.hallucination_rate": 0.5},
            {},
        ]
    )
    assert agg == {
        "retrieval.recall@5": 0.75,
        "answer.abstention_accuracy": 0.5,
        "answer.hallucination_rate": 0.25,
        "cases": 3,
    }
    baseline = {"retrieval.recall@5": 0.9, "answer.hallucination_rate": 0.1, "latency_ms": 100}
    found = regressions({**agg, "latency_ms": 900}, baseline, max_drop=0.05)
    assert any(f.startswith("retrieval.recall@5") for f in found)  # dropped 0.15
    assert any(f.startswith("answer.hallucination_rate") for f in found)  # rose 0.15 (lower is better)
    assert not any(f.startswith("latency_ms") for f in found)  # reported, never gated
    assert regressions({"retrieval.recall@5": 0.88}, baseline, max_drop=0.05) == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            {"claims": 4, "supported_claims": 3, "answer_relevance": 5, "context_relevance": 0.5, "correctness": 3},
            {"faithfulness": 0.75, "hallucination_rate": 0.25, "answer_relevance": 1.0, "correctness": 0.5},
        ),
        (
            {"claims": 0, "supported_claims": 0, "answer_relevance": 1, "context_relevance": 3.0, "correctness": 9},
            {"faithfulness": 1.0, "answer_relevance": 0.0, "context_relevance": 1.0, "correctness": 1.0},
        ),
        (
            {"claims": 2, "supported_claims": 5, "answer_relevance": 3, "context_relevance": -1, "correctness": None},
            {"faithfulness": 1.0, "context_relevance": 0.0, "correctness": None},
        ),
    ],
)
async def test_judge_scores_are_normalized_and_clamped(raw: dict, expected: dict) -> None:
    llm = ScriptedLLM()
    llm.json_responses["answer_judgement"] = {"unsupported_claims": [], "abstained": False, "rationale": "r", **raw}
    out = await judge_answer(llm, "q", [], "answer", reference="ref" if raw["correctness"] is not None else None)
    for key, value in expected.items():
        assert out[key] == pytest.approx(value) if value is not None else out[key] is None
    assert llm.json_calls[0]["name"] == "answer_judgement"
