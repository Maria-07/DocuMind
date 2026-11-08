from __future__ import annotations

import uuid

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ConflictError, NotFoundError, ServiceUnavailableError, ValidationFailedError
from app.core.permissions import Permission, Principal
from app.models import Document, DocumentChunk, EvalCase, EvalCaseResult, EvalDataset, EvalRun
from app.repositories.documents import visible_documents_clause
from app.schemas.evaluation import (
    EvalCaseInput,
    EvalCaseRead,
    EvalCaseResultRead,
    EvalDatasetCreate,
    EvalDatasetRead,
    EvalRunDetail,
    EvalRunSummary,
    MetricPoint,
)
from app.services.audit import RequestMeta, record_audit
from app.services.job_queue import RUN_EVALUATION, JobQueue

log = structlog.get_logger(__name__)


class EvaluationService:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], queue: JobQueue) -> None:
        self.sessionmaker = sessionmaker
        self.queue = queue

    # --- per-document confidentiality ------------------------------------------------------
    # Runs execute as the user who started them, and cases are written by their author: results
    # and quotes may contain content from documents other viewers of the same dataset cannot see.
    @staticmethod
    async def _hidden_documents(s: AsyncSession, principal: Principal, document_ids: set[uuid.UUID]) -> set[uuid.UUID]:
        if not document_ids or principal.has(Permission.DOCUMENTS_ADMIN):
            return set()
        visible = set(
            (
                await s.execute(
                    select(Document.id).where(Document.id.in_(document_ids), visible_documents_clause(principal))
                )
            ).scalars()
        )
        return document_ids - visible

    # --- datasets ---------------------------------------------------------------------------
    async def _dataset(self, s: AsyncSession, principal: Principal, dataset_id: uuid.UUID) -> EvalDataset:
        ds = await s.get(EvalDataset, dataset_id)
        if ds is None or ds.organization_id != principal.organization_id:
            raise NotFoundError("Dataset not found", code="dataset_not_found")
        return ds

    async def list_datasets(self, principal: Principal) -> list[EvalDatasetRead]:
        async with self.sessionmaker() as s:
            datasets = (
                (
                    await s.execute(
                        select(EvalDataset)
                        .where(EvalDataset.organization_id == principal.organization_id)
                        .order_by(EvalDataset.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            counts = dict(
                (
                    await s.execute(
                        select(EvalCase.dataset_id, func.count())
                        .group_by(EvalCase.dataset_id)
                        .where(EvalCase.dataset_id.in_([d.id for d in datasets]))
                    )
                )
                .tuples()
                .all()
            )
            out = []
            for d in datasets:
                last = (
                    await s.execute(
                        select(EvalRun).where(EvalRun.dataset_id == d.id).order_by(EvalRun.created_at.desc()).limit(1)
                    )
                ).scalar_one_or_none()
                out.append(
                    EvalDatasetRead.model_validate(d).model_copy(
                        update={
                            "case_count": counts.get(d.id, 0),
                            "last_run": EvalRunSummary.model_validate(last) if last else None,
                        }
                    )
                )
            return out

    async def create_dataset(self, principal: Principal, data: EvalDatasetCreate, meta: RequestMeta) -> EvalDatasetRead:
        async with self.sessionmaker() as s:
            exists = await s.scalar(
                select(func.count()).where(
                    EvalDataset.organization_id == principal.organization_id, EvalDataset.name == data.name.strip()
                )
            )
            if exists:
                raise ConflictError("A dataset with this name already exists", code="dataset_name_taken")
            ds = EvalDataset(
                organization_id=principal.organization_id,
                name=data.name.strip(),
                description=data.description,
                created_by=principal.user_id,
            )
            s.add(ds)
            await s.flush()
            record_audit(
                s,
                action="evaluation.dataset_created",
                meta=meta,
                organization_id=principal.organization_id,
                actor_id=principal.user_id,
                resource_type="eval_dataset",
                resource_id=ds.id,
            )
            await s.commit()
            return EvalDatasetRead.model_validate(ds)

    async def delete_dataset(self, principal: Principal, dataset_id: uuid.UUID, meta: RequestMeta) -> None:
        async with self.sessionmaker() as s:
            ds = await self._dataset(s, principal, dataset_id)
            await s.delete(ds)
            record_audit(
                s,
                action="evaluation.dataset_deleted",
                meta=meta,
                organization_id=principal.organization_id,
                actor_id=principal.user_id,
                resource_type="eval_dataset",
                resource_id=dataset_id,
            )
            await s.commit()

    # --- cases ------------------------------------------------------------------------------
    async def list_cases(self, principal: Principal, dataset_id: uuid.UUID) -> list[EvalCaseRead]:
        async with self.sessionmaker() as s:
            await self._dataset(s, principal, dataset_id)
            cases = (
                (
                    await s.execute(
                        select(EvalCase).where(EvalCase.dataset_id == dataset_id).order_by(EvalCase.created_at)
                    )
                )
                .scalars()
                .all()
            )
            hidden = await self._hidden_documents(
                s,
                principal,
                {d for c in cases for d in c.relevant_document_ids}
                | {uuid.UUID(str(e["document_id"])) for c in cases for e in c.evidence if e.get("document_id")},
            )
            out = []
            for c in cases:
                read = EvalCaseRead.model_validate(c)
                if hidden & set(c.relevant_document_ids):
                    read = read.model_copy(
                        update={
                            "expected_answer": None,
                            "evidence": [{"document_id": e.get("document_id"), "quote": None} for e in c.evidence],
                            "redacted": True,
                        }
                    )
                out.append(read)
            return out

    async def _check_documents(self, s: AsyncSession, principal: Principal, cases: list[EvalCaseInput]) -> None:
        ids = {d for c in cases for d in c.relevant_document_ids} | {e.document_id for c in cases for e in c.evidence}
        if not ids:
            return
        found = set(
            (
                await s.execute(
                    select(Document.id).where(
                        Document.id.in_(ids),
                        Document.organization_id == principal.organization_id,
                        Document.deleted_at.is_(None),
                    )
                )
            ).scalars()
        )
        missing = [str(i) for i in ids - found]
        if missing:
            raise ValidationFailedError(
                "Unknown documents in the cases", code="unknown_documents", details={"document_ids": missing}
            )
        for c in cases:
            if any(e.document_id not in c.relevant_document_ids for e in c.evidence):
                raise ValidationFailedError(
                    "Evidence must come from one of the case's relevant documents", code="evidence_document_mismatch"
                )

    async def add_cases(
        self, principal: Principal, dataset_id: uuid.UUID, cases: list[EvalCaseInput]
    ) -> list[EvalCaseRead]:
        async with self.sessionmaker() as s:
            await self._dataset(s, principal, dataset_id)
            await self._check_documents(s, principal, cases)
            rows = [
                EvalCase(
                    dataset_id=dataset_id,
                    question=c.question.strip(),
                    expected_answer=c.expected_answer,
                    relevant_document_ids=c.relevant_document_ids,
                    evidence=[e.model_dump(mode="json") for e in c.evidence],
                    tags=c.tags,
                    relevant_chunk_ids=[],
                )
                for c in cases
            ]
            s.add_all(rows)
            await s.commit()
            return [EvalCaseRead.model_validate(r) for r in rows]

    async def delete_case(self, principal: Principal, dataset_id: uuid.UUID, case_id: uuid.UUID) -> None:
        async with self.sessionmaker() as s:
            await self._dataset(s, principal, dataset_id)
            case = await s.get(EvalCase, case_id)
            if case is None or case.dataset_id != dataset_id:
                raise NotFoundError("Case not found", code="case_not_found")
            await s.delete(case)
            await s.commit()

    # --- runs -------------------------------------------------------------------------------
    async def start_run(
        self, principal: Principal, dataset_id: uuid.UUID, baseline_run_id: uuid.UUID | None, meta: RequestMeta
    ) -> EvalRunSummary:
        async with self.sessionmaker() as s:
            await self._dataset(s, principal, dataset_id)
            cases = await s.scalar(select(func.count()).where(EvalCase.dataset_id == dataset_id))
            if not cases:
                raise ValidationFailedError("Add at least one case before running", code="empty_dataset")
            active = await s.scalar(
                select(func.count()).where(EvalRun.dataset_id == dataset_id, EvalRun.status.in_(["queued", "running"]))
            )
            if active:
                raise ConflictError("A run for this dataset is already in progress", code="run_in_progress")
            if baseline_run_id is not None:
                base = await s.get(EvalRun, baseline_run_id)
                if base is None or base.dataset_id != dataset_id:
                    raise ValidationFailedError("Baseline must be a run of the same dataset", code="invalid_baseline")
            else:
                baseline_run_id = await s.scalar(
                    select(EvalRun.id)
                    .where(EvalRun.dataset_id == dataset_id, EvalRun.status == "complete")
                    .order_by(EvalRun.created_at.desc())
                    .limit(1)
                )
            run = EvalRun(
                dataset_id=dataset_id,
                organization_id=principal.organization_id,
                triggered_by=principal.user_id,
                status="queued",
                baseline_run_id=baseline_run_id,
                case_count=int(cases),
                completed_cases=0,
                config={},
                metrics={},
            )
            s.add(run)
            await s.flush()
            record_audit(
                s,
                action="evaluation.run_started",
                meta=meta,
                organization_id=principal.organization_id,
                actor_id=principal.user_id,
                resource_type="eval_run",
                resource_id=run.id,
            )
            await s.commit()
        try:
            await self.queue.enqueue(RUN_EVALUATION, str(run.id), job_id=f"eval:{run.id}")
        except Exception as exc:
            async with self.sessionmaker() as s:
                r = await s.get(EvalRun, run.id)
                if r is not None:
                    r.status, r.error = "failed", "Could not queue the run"
                    await s.commit()
            raise ServiceUnavailableError("Could not queue the evaluation run", code="queue_unavailable") from exc
        return EvalRunSummary.model_validate(run)

    async def list_runs(
        self, principal: Principal, dataset_id: uuid.UUID | None, limit: int = 50
    ) -> list[EvalRunSummary]:
        async with self.sessionmaker() as s:
            stmt = select(EvalRun).where(EvalRun.organization_id == principal.organization_id)
            if dataset_id is not None:
                stmt = stmt.where(EvalRun.dataset_id == dataset_id)
            runs = (await s.execute(stmt.order_by(EvalRun.created_at.desc()).limit(limit))).scalars().all()
            return [EvalRunSummary.model_validate(r) for r in runs]

    async def get_run(self, principal: Principal, run_id: uuid.UUID) -> EvalRunDetail:
        async with self.sessionmaker() as s:
            run = await s.get(EvalRun, run_id)
            if run is None or run.organization_id != principal.organization_id:
                raise NotFoundError("Run not found", code="run_not_found")
            results = (
                (
                    await s.execute(
                        select(EvalCaseResult)
                        .where(EvalCaseResult.run_id == run_id)
                        .order_by(EvalCaseResult.created_at)
                    )
                )
                .scalars()
                .all()
            )
            baseline = await s.get(EvalRun, run.baseline_run_id) if run.baseline_run_id else None
            # Documents each answer drew on: everything retrieved for it, plus what it cites.
            chunk_ids = {c for r in results for c in r.retrieved_chunk_ids}
            chunk_doc: dict[uuid.UUID, uuid.UUID] = {}
            if chunk_ids and not principal.has(Permission.DOCUMENTS_ADMIN):
                rows = await s.execute(
                    select(DocumentChunk.id, DocumentChunk.document_id).where(DocumentChunk.id.in_(chunk_ids))
                )
                chunk_doc = {chunk_id: document_id for chunk_id, document_id in rows.all()}

            def used(r: EvalCaseResult) -> set[uuid.UUID]:
                docs = {chunk_doc[c] for c in r.retrieved_chunk_ids if c in chunk_doc}
                return docs | {uuid.UUID(str(c["document_id"])) for c in r.citations if c.get("document_id")}

            hidden = await self._hidden_documents(s, principal, {d for r in results for d in used(r)})
            reads = []
            for r in results:
                read = EvalCaseResultRead.model_validate(r)
                if hidden & used(r):
                    read = read.model_copy(update={"answer": None, "citations": [], "judge": {}, "redacted": True})
                reads.append(read)
            return EvalRunDetail(
                **EvalRunSummary.model_validate(run).model_dump(),
                results=reads,
                baseline_metrics=baseline.metrics if baseline else None,
            )

    async def metric_history(self, principal: Principal, dataset_id: uuid.UUID) -> list[MetricPoint]:
        async with self.sessionmaker() as s:
            await self._dataset(s, principal, dataset_id)
            runs = (
                (
                    await s.execute(
                        select(EvalRun)
                        .where(EvalRun.dataset_id == dataset_id, EvalRun.status == "complete")
                        .order_by(EvalRun.finished_at)
                    )
                )
                .scalars()
                .all()
            )
            return [
                MetricPoint(run_id=r.id, finished_at=r.finished_at, metrics=r.metrics, config=r.config) for r in runs
            ]
