"""Retrieval accuracy on a labelled set with the real models (bge-base, BM25, MiniLM cross-encoder).

Opt-in:  RUN_MODEL_TESTS=1 TEST_QDRANT_URL=... pytest tests/integration/test_retrieval_accuracy.py -s

Each question's relevant chunk is identified by content (a phrase only that chunk contains), so the
labels survive re-chunking. Questions are paraphrased — they avoid the documents' wording.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qm
from sqlalchemy import select

from app.core.permissions import Principal, permissions_for
from app.core.resources import AppResources
from app.models import DocumentChunk
from app.models.enums import Role
from app.rag.evaluation.metrics import mean, ndcg_at_k, recall_at_k, reciprocal_rank
from app.rag.ingestion.pipeline import IngestionPipeline
from app.rag.reranking import FastEmbedReranker
from app.rag.retrieval import Retriever
from app.rag.setup import build_ingestion_pipeline
from tests.documents import build_docx, build_pdf, build_xlsx
from tests.integration.api_helpers import register, upload_one
from tests.integration.rag_helpers import run_ingestion

TEST_QDRANT_URL = os.environ.get("TEST_QDRANT_URL")
MODEL_CACHE = Path(__file__).resolve().parents[2] / ".models"
pytestmark = pytest.mark.skipif(
    not (TEST_QDRANT_URL and os.environ.get("RUN_MODEL_TESTS")), reason="RUN_MODEL_TESTS / TEST_QDRANT_URL not set"
)

HANDBOOK_PAGES = [
    "Annual leave. Full-time employees accrue twenty-five days of paid vacation per calendar year. "
    "Unused vacation of up to five days may be carried over until the end of March.",
    "Remote work. Staff may work from home up to three days per week with their manager's approval. "
    "The company reimburses a one-time home office allowance of 400 euros.",
    "Travel expenses. Economy class is required for flights under six hours. Hotel costs are covered "
    "up to 180 euros per night; receipts must be submitted within thirty days of travel.",
    "Parental leave. Birth parents receive sixteen weeks of fully paid leave, and partners receive "
    "six weeks. Leave can be split into at most two blocks within the first year.",
    "Code of conduct. Gifts from suppliers worth more than 50 euros must be declared to the compliance "
    "team. Conflicts of interest have to be reported before a contract is signed.",
]

# Near-miss distractors: same domain, overlapping vocabulary, different facts.
DISTRACTOR_PAGES = [
    "Sick leave. Employees who are ill must notify their manager before 9 am. A doctor's note is required "
    "from the fourth consecutive day of absence.",
    "Public holidays. The office is closed on national public holidays. Staff working on a public holiday "
    "receive a compensatory day off within two months.",
    "Bereavement leave. Up to five paid days are granted after the death of a close family member.",
    "Unpaid leave. Requests for unpaid leave longer than two weeks need approval from the HR director.",
    "Training budget. Each employee has an annual learning budget of 1,500 euros for courses and books.",
    "Company cars. Field sales staff may lease a company car; private use is taxed as a benefit in kind.",
    "Mileage. Business trips with a private car are reimbursed at 0.30 euros per kilometre.",
    "Meal expenses. During business travel, meals are reimbursed up to 45 euros per day with receipts.",
    "Per diem. International trips longer than eight hours qualify for a daily allowance of 60 euros.",
    "Conference travel. Attending external conferences requires a written business case and budget owner "
    "approval at least four weeks in advance.",
    "Rail travel. Train journeys under four hours should be booked in second class.",
    "Overtime. Overtime beyond ten hours per month must be approved in advance and is compensated with time off.",
    "Probation. New hires complete a six-month probation period with reviews after three and six months.",
    "Notice period. After probation, the notice period is three months to the end of a calendar month.",
    "Performance reviews. Reviews take place twice a year, in June and December.",
    "Pension. The company contributes four percent of the base salary to the occupational pension plan.",
    "Health insurance. Supplementary dental insurance is offered to all permanent employees.",
    "Relocation. Employees relocating at the company's request receive up to 5,000 euros in moving costs.",
    "IT equipment. Laptops are replaced every four years; personal devices may not store customer data.",
    "VPN usage. Access to internal systems from outside the office is only permitted through the company VPN.",
    "Encryption. All laptops must use full-disk encryption and lock automatically after five minutes.",
    "Access reviews. Managers review their team's system access every quarter and remove unused accounts.",
    "Incident reporting. Suspected security incidents must be reported to the security team within one hour.",
    "Clean desk. Confidential papers must be locked away at the end of each working day.",
]

# (question, phrase that only the relevant chunk contains)
LABELLED = [
    ("How many vacation days do full-time staff get each year?", "twenty-five days of paid vacation"),
    ("Can I keep holiday I didn't use?", "carried over until the end of March"),
    ("How often am I allowed to work from home?", "three days per week"),
    ("Is there money for setting up a home office?", "home office allowance"),
    ("Which seat class should I book for a short flight?", "Economy class"),
    ("What is the nightly limit for hotels on business trips?", "180 euros per night"),
    ("How long is maternity leave?", "sixteen weeks"),
    ("Do I need to report presents from vendors?", "Gifts from suppliers"),
    ("How long must passwords be and when do they change?", "14 characters"),
    ("Is two-step login required when working remotely?", "Multi-factor authentication"),
    ("How long are client files kept?", "seven years"),
    ("What did EMEA earn in the first quarter?", "Region: EMEA | Quarter: Q1"),
]


def _norm(text: str) -> str:
    return " ".join(text.split())  # PDF line wrapping must not break label matching


@dataclass
class RealRetrieval:
    pipeline: IngestionPipeline
    retriever: Retriever
    org_ids: list[str] = field(default_factory=list)  # tenants created by the test, cleaned up after


@pytest.fixture
async def real_retrieval(resources: AppResources) -> AsyncIterator[RealRetrieval]:
    resources.settings.model_cache_dir = MODEL_CACHE
    resources.settings.qdrant_collection_alias = f"test_alias_{uuid.uuid4().hex[:8]}"
    client = AsyncQdrantClient(url=TEST_QDRANT_URL)
    pipeline = await build_ingestion_pipeline(
        settings=resources.settings, sessionmaker=resources.sessionmaker, storage=resources.storage, qdrant=client
    )
    holder = RealRetrieval(
        pipeline=pipeline,
        retriever=Retriever(
            settings=resources.settings,
            sessionmaker=resources.sessionmaker,
            client=client,
            collection=pipeline.store.alias,
            embedder=pipeline.embedder,
            sparse=pipeline.sparse,
            reranker=FastEmbedReranker(resources.settings.reranker_model, cache_dir=MODEL_CACHE),
        ),
    )
    yield holder
    for org in holder.org_ids:
        await client.delete(
            pipeline.collection,
            points_selector=qm.FilterSelector(
                filter=qm.Filter(must=[qm.FieldCondition(key="org_id", match=qm.MatchValue(value=org))])
            ),
            wait=True,
        )
    await client.update_collection_aliases(
        change_aliases_operations=[
            qm.DeleteAliasOperation(delete_alias=qm.DeleteAlias(alias_name=pipeline.store.alias))
        ]
    )
    await client.close()


async def test_retrieval_accuracy_on_labelled_questions(
    client: httpx.AsyncClient, real_retrieval: RealRetrieval, resources: AppResources
) -> None:
    pipeline, retriever = real_retrieval.pipeline, real_retrieval.retriever
    admin = await register(client)
    real_retrieval.org_ids.append(admin.org_id)
    for name, data in [
        ("Employee Handbook.pdf", build_pdf(HANDBOOK_PAGES, title="Employee Handbook")),
        ("HR and IT Guidelines.pdf", build_pdf(DISTRACTOR_PAGES, title="HR and IT Guidelines")),
        ("Security Policy.docx", build_docx()),
        ("Revenue 2026.xlsx", build_xlsx(rows=12)),
    ]:
        doc = await upload_one(admin, name, data)
        assert await run_ingestion(pipeline, resources, doc["id"]) == "indexed"

    async with resources.sessionmaker() as s:
        chunks = (await s.execute(select(DocumentChunk))).scalars().all()
    relevant: dict[str, set[uuid.UUID]] = {}
    for question, phrase in LABELLED:
        ids = {c.id for c in chunks if _norm(phrase) in _norm(c.text)}
        assert ids, f"label phrase not found in any chunk: {phrase!r}"
        relevant[question] = ids

    principal = Principal(
        user_id=uuid.UUID(admin.user_id),
        organization_id=uuid.UUID(admin.org_id),
        role=Role.ADMIN,
        email="a@example.com",
        permissions=permissions_for(Role.ADMIN),
    )

    report: dict[str, dict[str, float]] = {}
    ranks: dict[str, list[int | None]] = {}
    for mode in ("semantic", "hybrid"):
        for rerank in (False, True):
            name = f"{mode}{' + rerank' if rerank else ''}"
            r1, r5, rr, nd = [], [], [], []
            ranks[name] = []
            for question, _ in LABELLED:
                result = await retriever.retrieve(
                    principal, question, mode=mode, top_n=10, rerank=rerank, apply_threshold=False, diversify=False
                )
                assert all((c.relevance is not None) == rerank for c in result.chunks)
                ranked = [c.chunk_id for c in result.chunks]
                rel = relevant[question]
                ranks[name].append(next((i for i, cid in enumerate(ranked, 1) if cid in rel), None))
                r1.append(recall_at_k(ranked, rel, 1))
                r5.append(recall_at_k(ranked, rel, 5))
                rr.append(reciprocal_rank(ranked, rel))
                nd.append(ndcg_at_k(ranked, rel, 5))
            report[name] = {"recall@1": mean(r1), "recall@5": mean(r5), "mrr": mean(rr), "ndcg@5": mean(nd)}

    print(f"\nRetrieval accuracy ({len(LABELLED)} paraphrased questions, 4 documents, {len(chunks)} chunks):")
    print(f"{'configuration':<22}{'R@1':>7}{'R@5':>7}{'MRR':>7}{'NDCG@5':>8}   rank of relevant chunk per question")
    for name, m in report.items():
        per_q = " ".join("-" if r is None else str(r) for r in ranks[name])
        print(f"{name:<22}{m['recall@1']:>7.2f}{m['recall@5']:>7.2f}{m['mrr']:>7.2f}{m['ndcg@5']:>8.2f}   {per_q}")

    best = report["hybrid + rerank"]
    assert best["recall@5"] >= 0.9, report
    assert best["mrr"] >= 0.75, report

    # Calibration of the "no answer" threshold: top reranker relevance per question.
    async def top_relevance(q: str) -> float:
        r = await retriever.retrieve(principal, q, top_n=1, apply_threshold=False, diversify=False)
        return r.chunks[0].relevance or 0.0 if r.chunks else 0.0

    answerable = [await top_relevance(q) for q, _ in LABELLED]
    unanswerable = [
        await top_relevance(q)
        for q in (
            "What is the capital of Peru?",
            "Who won the football world cup in 2014?",
            "How do I bake sourdough bread?",
            "What is our company's stock ticker symbol?",
            "What is the dress code for the summer party?",
        )
    ]
    threshold = resources.settings.retrieval_min_relevance
    print(
        f"top relevance — answerable: min {min(answerable):.2e} median {sorted(answerable)[len(answerable) // 2]:.2e}"
        f" | unanswerable: max {max(unanswerable):.2e} (all: {', '.join(f'{u:.1e}' for u in unanswerable)})"
        f" | threshold {threshold}"
    )
    assert min(answerable) >= threshold, (answerable, threshold)
    assert max(unanswerable) < threshold, (unanswerable, threshold)
