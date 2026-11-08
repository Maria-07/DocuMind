"""Evaluation runner: runs a dataset through the real retrieval + generation path and scores it.

Per case:
  * retrieval metrics on the top-10 ranking (no threshold): chunk-level P@5, R@5, MRR, NDCG@5 when the
    case has evidence quotes / pinned chunks, and document-level recall@5 / MRR always;
  * the answer exactly as chat would produce it (same thresholds, prompt and citation validation);
  * citation precision (citations pointing at relevant chunks/documents), abstention accuracy for
    unanswerable cases, and LLM-judged faithfulness / hallucination rate / answer relevance /
    context relevance / correctness.
Runs use the triggering user's permissions — an evaluation can never see documents that user can't.
"""

from __future__ import annotations

import re
import time
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.permissions import Principal, permissions_for
from app.models import Document, DocumentChunk, EmbeddingConfig, EvalCase, EvalCaseResult, EvalRun
from app.rag.answering import CitationTracker, to_source_documents
from app.rag.evaluation.judge import JUDGE_VERSION, judge_answer
from app.rag.evaluation.metrics import mean, ndcg_at_k, precision_at_k, recall_at_k, reciprocal_rank
from app.rag.generation.base import GenerationError, LLMProvider
from app.rag.generation.prompts import PROMPT_VERSION
from app.rag.retrieval import Retriever
from app.repositories.users import UserRepository
from app.schemas.organization import OrganizationSettings

log = structlog.get_logger(__name__)

K = 5
_WS = re.compile(r"\s+")

# Metrics where lower is better (for regression checks).
LOWER_IS_BETTER = {"answer.hallucination_rate", "latency_ms"}


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip().lower()


def run_config(settings: Settings, judge_model: str | None) -> dict[str, Any]:
    """Everything that can change results — stored with each run for comparison."""
    return {
        "embedding_model": settings.embedding_model,
        "sparse_model": settings.sparse_model,
        "chunk_size": settings.chunk_size_tokens,
        "chunk_overlap": settings.chunk_overlap_tokens,
        "reranker": settings.reranker_model if settings.reranker_enabled else None,
        "rerank_candidates": settings.rerank_candidates,
        "retrieval_candidates": settings.retrieval_candidates,
        "top_n": settings.retrieval_top_n,
        "min_relevance": settings.retrieval_min_relevance,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "llm_effort": settings.llm_effort,
        "prompt_version": PROMPT_VERSION,
        "judge_model": judge_model,
        "judge_version": JUDGE_VERSION,
    }


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    values: dict[str, list[float]] = defaultdict(list)
    for metrics in results:
        for key, value in metrics.items():
            if isinstance(value, bool):
                values[key].append(1.0 if value else 0.0)
            elif isinstance(value, int | float):
                values[key].append(float(value))
    out: dict[str, Any] = {key: round(mean(v), 4) for key, v in values.items()}
    out["cases"] = len(results)
    return out


def regressions(current: dict[str, Any], baseline: dict[str, Any], max_drop: float) -> list[str]:
    """Metrics that got worse than the baseline by more than ``max_drop`` (absolute)."""
    found = []
    for key, value in current.items():
        if key == "cases" or key not in baseline or not isinstance(value, int | float):
            continue
        delta = value - baseline[key]
        if key == "latency_ms":
            continue  # latency is environment-dependent; reported, not gated
        worse = delta > max_drop if key in LOWER_IS_BETTER else -delta > max_drop
        if worse:
            found.append(f"{key}: {baseline[key]:.3f} -> {value:.3f}")
    return found


class EvaluationRunner:
    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
        retriever: Retriever,
        llm: LLMProvider | None,
        judge: LLMProvider | None = None,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.settings = settings
        self.retriever = retriever
        self.llm = llm
        self.judge = judge or llm

    async def _principal(self, user_id: uuid.UUID) -> Principal:
        async with self.sessionmaker() as s:
            users = UserRepository(s)
            found = await users.get_with_organization(user_id)
            if found is None:
                raise ValueError("The user who started this evaluation no longer exists")
            user, org = found
            groups = await users.group_ids_for(user.id)
        settings = OrganizationSettings.model_validate(org.settings)
        return Principal(
            user_id=user.id,
            organization_id=org.id,
            role=user.role,
            email=user.email,
            group_ids=groups,
            permissions=permissions_for(user.role, users_can_upload=settings.users_can_upload),
        )

    async def _relevant_chunks(self, case: EvalCase) -> set[uuid.UUID]:
        relevant = set(case.relevant_chunk_ids)
        quotes: dict[uuid.UUID, list[str]] = defaultdict(list)
        for ev in case.evidence or []:
            if ev.get("document_id") and ev.get("quote"):
                quotes[uuid.UUID(str(ev["document_id"]))].append(_norm(str(ev["quote"])))
        if quotes:
            async with self.sessionmaker() as s:
                docs = (await s.execute(select(Document).where(Document.id.in_(list(quotes))))).scalars().all()
                for d in docs:
                    rows = (
                        await s.execute(
                            select(DocumentChunk.id, DocumentChunk.text).where(
                                DocumentChunk.document_id == d.id,
                                DocumentChunk.index_version == d.active_index_version,
                            )
                        )
                    ).all()
                    for chunk_id, text in rows:
                        if any(q in _norm(text) for q in quotes[d.id]):
                            relevant.add(chunk_id)
        return relevant

    async def evaluate_case(self, principal: Principal, case: EvalCase) -> tuple[dict[str, Any], dict[str, Any]]:
        """Returns (fields for EvalCaseResult, metrics)."""
        started = time.perf_counter()
        metrics: dict[str, Any] = {}
        relevant_docs = set(case.relevant_document_ids)
        relevant_chunks = await self._relevant_chunks(case)
        answerable = bool(relevant_docs)

        # --- retrieval quality (ranking, no threshold) ------------------------------------
        ranking = await self.retriever.retrieve(
            principal, case.question, top_n=10, apply_threshold=False, diversify=False
        )
        ranked = [c.chunk_id for c in ranking.chunks]
        ranked_docs = list(dict.fromkeys(c.document_id for c in ranking.chunks))
        if relevant_chunks:
            metrics["retrieval.precision@5"] = precision_at_k(ranked, relevant_chunks, K)
            metrics["retrieval.recall@5"] = recall_at_k(ranked, relevant_chunks, K)
            metrics["retrieval.mrr"] = reciprocal_rank(ranked, relevant_chunks)
            metrics["retrieval.ndcg@5"] = ndcg_at_k(ranked, relevant_chunks, K)
        if relevant_docs:
            metrics["retrieval.doc_recall@5"] = recall_at_k(ranked_docs, relevant_docs, K)
            metrics["retrieval.doc_mrr"] = reciprocal_rank(ranked_docs, relevant_docs)

        # --- the answer, exactly as chat produces it ----------------------------------------
        fields: dict[str, Any] = {
            "retrieved_chunk_ids": ranked,
            "relevant_chunk_ids": list(relevant_chunks),
            "top_relevance": ranking.chunks[0].relevance if ranking.chunks else None,
        }
        context = await self.retriever.retrieve(principal, case.question, token_budget=self.settings.context_max_tokens)
        judge: dict[str, Any] = {}
        tokens = 0
        if not context.chunks or self.llm is None:
            answer, citations, abstained = None, [], True
        else:
            sources = to_source_documents(context.chunks)
            tracker = CitationTracker(context.chunks, sources)
            async for event in self.llm.stream_answer(question=case.question, sources=sources, history=[]):
                for _ in tracker.feed(event):
                    pass
            answer = tracker.answer
            usage = tracker.done.usage if tracker.done else {}
            tokens += int(usage.get("input_tokens", 0) or 0) + int(usage.get("output_tokens", 0) or 0)
            citations = [
                {
                    "index": c.number,
                    "chunk_id": str(c.chunk.chunk_id),
                    "document_id": str(c.chunk.document_id),
                    "document_title": c.chunk.document_title,
                    "location": c.chunk.location,
                }
                for c in tracker.ordered_citations
            ]
            metrics["answer.citations"] = len(citations)
            metrics["answer.citations_rejected"] = tracker.rejected
            if citations and answerable:
                hits = [
                    (uuid.UUID(str(c["chunk_id"])) in relevant_chunks)
                    if relevant_chunks
                    else (uuid.UUID(str(c["document_id"])) in relevant_docs)
                    for c in citations
                ]
                metrics["answer.citation_precision"] = sum(hits) / len(hits)
            if self.judge is not None and answer:
                judge = await judge_answer(self.judge, case.question, context.chunks, answer, case.expected_answer)
                abstained = judge["abstained"]
                for key in (
                    "faithfulness",
                    "hallucination_rate",
                    "answer_relevance",
                    "context_relevance",
                    "correctness",
                ):
                    if judge.get(key) is not None:
                        metrics[f"answer.{key}"] = judge[key]
            else:
                abstained = False
        # Abstaining is right exactly when the documents don't answer the question.
        metrics["answer.abstention_accuracy"] = abstained == (not answerable)
        metrics["latency_ms"] = int((time.perf_counter() - started) * 1000)
        fields.update(
            answer=answer,
            citations=citations,
            judge=judge,
            cost_tokens=tokens or None,
            latency_ms=metrics["latency_ms"],
        )
        return fields, metrics

    async def run(self, run_id: uuid.UUID) -> EvalRun:
        async with self.sessionmaker() as s:
            run = await s.get(EvalRun, run_id)
            if run is None:
                raise ValueError("Evaluation run not found")
            cases = (
                (
                    await s.execute(
                        select(EvalCase).where(EvalCase.dataset_id == run.dataset_id).order_by(EvalCase.created_at)
                    )
                )
                .scalars()
                .all()
            )
            config_id = await s.scalar(select(EmbeddingConfig.id).where(EmbeddingConfig.is_active.is_(True)))
            run.status, run.started_at, run.case_count = "running", datetime.now(UTC), len(cases)
            judge_model = getattr(self.judge, "model_id", None)
            run.config = {
                **run_config(self.settings, judge_model),
                "embedding_config_id": str(config_id) if config_id else None,
            }
            await s.commit()
            triggered_by = run.triggered_by

        all_metrics: list[dict[str, Any]] = []
        try:
            if triggered_by is None:
                raise ValueError("The user who started this evaluation no longer exists")
            principal = await self._principal(triggered_by)
            for case in cases:
                try:
                    fields, metrics = await self.evaluate_case(principal, case)
                    error = None
                except GenerationError as exc:
                    fields, metrics, error = {}, {}, f"{exc.code}: {exc.message}"
                all_metrics.append(metrics)
                async with self.sessionmaker() as s:
                    s.add(
                        EvalCaseResult(
                            run_id=run_id,
                            case_id=case.id,
                            question=case.question,
                            metrics=metrics,
                            error=error,
                            **fields,
                        )
                    )
                    r = await s.get(EvalRun, run_id)
                    assert r is not None
                    r.completed_cases += 1
                    await s.commit()
            status, error_text = "complete", None
        except Exception as exc:
            log.exception("evaluation_run_failed", run_id=str(run_id))
            status, error_text = "failed", str(exc)[:500]

        async with self.sessionmaker() as s:
            run = await s.get(EvalRun, run_id)
            assert run is not None
            run.metrics = aggregate([m for m in all_metrics if m])
            run.status, run.error, run.finished_at = status, error_text, datetime.now(UTC)
            await s.commit()
            log.info("evaluation_run_finished", run_id=str(run_id), status=status, cases=len(all_metrics))
            return run
