"""Comparisons, evaluations, analytics and embedding administration."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import (
    CurrentPrincipal,
    DbSession,
    Meta,
    Resources,
    rate_limit_by_user,
    require_permission,
    require_rag,
)
from app.core.permissions import Permission, Principal
from app.models import DocumentComparison
from app.schemas.common import ERROR_RESPONSES
from app.schemas.evaluation import (
    EvalCaseInput,
    EvalCaseRead,
    EvalDatasetCreate,
    EvalDatasetRead,
    EvalRunCreate,
    EvalRunDetail,
    EvalRunSummary,
    MetricPoint,
)
from app.services.analytics import AnalyticsService
from app.services.audit import record_audit
from app.services.comparison import ComparisonService
from app.services.embeddings import embedding_overview, queue_reindex
from app.services.evaluation import EvaluationService

# --- comparisons ---------------------------------------------------------------------------

comparisons = APIRouter(prefix="/comparisons", tags=["comparisons"], responses=ERROR_RESPONSES)


class ComparisonCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_ids: list[uuid.UUID] = Field(min_length=2, max_length=5)
    question: str | None = Field(default=None, max_length=1000)


class ComparisonRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_ids: list[uuid.UUID]
    question: str | None
    status: str
    result: dict[str, Any]
    model: str | None
    latency_ms: int | None
    error: str | None
    created_at: Any


def _comparisons(resources: Resources) -> ComparisonService:
    return ComparisonService(resources.sessionmaker, require_rag(resources))


@comparisons.post(
    "",
    response_model=ComparisonRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_by_user("chat", "rate_limit_chat_per_minute"))],
    summary="Compare 2-5 documents (optionally around a question); structured, cited result",
)
async def create_comparison(
    data: ComparisonCreate, principal: CurrentPrincipal, service: Annotated[ComparisonService, Depends(_comparisons)]
) -> DocumentComparison:
    return await service.compare(principal, data.document_ids, data.question)


@comparisons.get("", response_model=list[ComparisonRead], summary="The caller's recent comparisons")
async def list_comparisons(
    principal: CurrentPrincipal, service: Annotated[ComparisonService, Depends(_comparisons)]
) -> list[DocumentComparison]:
    return await service.list(principal)


@comparisons.get("/{comparison_id}", response_model=ComparisonRead)
async def get_comparison(
    comparison_id: uuid.UUID, principal: CurrentPrincipal, service: Annotated[ComparisonService, Depends(_comparisons)]
) -> DocumentComparison:
    return await service.get(principal, comparison_id)


# --- evaluations ---------------------------------------------------------------------------

evaluations = APIRouter(prefix="/evaluations", tags=["evaluations"], responses=ERROR_RESPONSES)
Viewer = Annotated[Principal, Depends(require_permission(Permission.EVALUATIONS_VIEW))]
Runner = Annotated[Principal, Depends(require_permission(Permission.EVALUATIONS_RUN))]


def _evaluations(resources: Resources) -> EvaluationService:
    return EvaluationService(resources.sessionmaker, resources.job_queue)


EvalService = Annotated[EvaluationService, Depends(_evaluations)]


@evaluations.get("/datasets", response_model=list[EvalDatasetRead])
async def list_datasets(principal: Viewer, service: EvalService) -> list[EvalDatasetRead]:
    return await service.list_datasets(principal)


@evaluations.post("/datasets", response_model=EvalDatasetRead, status_code=status.HTTP_201_CREATED)
async def create_dataset(
    data: EvalDatasetCreate, principal: Runner, service: EvalService, meta: Meta
) -> EvalDatasetRead:
    return await service.create_dataset(principal, data, meta)


@evaluations.delete("/datasets/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_dataset(dataset_id: uuid.UUID, principal: Runner, service: EvalService, meta: Meta) -> Response:
    await service.delete_dataset(principal, dataset_id, meta)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@evaluations.get("/datasets/{dataset_id}/cases", response_model=list[EvalCaseRead])
async def list_cases(dataset_id: uuid.UUID, principal: Viewer, service: EvalService) -> list[EvalCaseRead]:
    return await service.list_cases(principal, dataset_id)


@evaluations.post(
    "/datasets/{dataset_id}/cases",
    response_model=list[EvalCaseRead],
    status_code=status.HTTP_201_CREATED,
    summary="Add one or more labelled cases (also used for bulk import)",
)
async def add_cases(
    dataset_id: uuid.UUID,
    cases: Annotated[list[EvalCaseInput], Body(min_length=1, max_length=500)],
    principal: Runner,
    service: EvalService,
) -> list[EvalCaseRead]:
    return await service.add_cases(principal, dataset_id, cases)


@evaluations.delete(
    "/datasets/{dataset_id}/cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
async def delete_case(dataset_id: uuid.UUID, case_id: uuid.UUID, principal: Runner, service: EvalService) -> Response:
    await service.delete_case(principal, dataset_id, case_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@evaluations.post("/run", response_model=EvalRunSummary, status_code=status.HTTP_202_ACCEPTED)
async def run_evaluation(data: EvalRunCreate, principal: Runner, service: EvalService, meta: Meta) -> EvalRunSummary:
    return await service.start_run(principal, data.dataset_id, data.baseline_run_id, meta)


@evaluations.get("/results", response_model=list[EvalRunSummary], summary="Evaluation runs (newest first)")
async def list_runs(
    principal: Viewer, service: EvalService, dataset_id: uuid.UUID | None = None
) -> list[EvalRunSummary]:
    return await service.list_runs(principal, dataset_id)


@evaluations.get("/results/{run_id}", response_model=EvalRunDetail, summary="A run with per-case results")
async def get_run(run_id: uuid.UUID, principal: Viewer, service: EvalService) -> EvalRunDetail:
    return await service.get_run(principal, run_id)


@evaluations.get("/metrics", response_model=list[MetricPoint], summary="Metric history of a dataset (trend)")
async def metric_history(dataset_id: uuid.UUID, principal: Viewer, service: EvalService) -> list[MetricPoint]:
    return await service.metric_history(principal, dataset_id)


# --- analytics -----------------------------------------------------------------------------

analytics = APIRouter(prefix="/analytics", tags=["analytics"], responses=ERROR_RESPONSES)
AnalyticsViewer = Annotated[Principal, Depends(require_permission(Permission.ANALYTICS_ORGANIZATION))]
Days = Annotated[int, Query(ge=7, le=180)]


@analytics.get("/overview")
async def analytics_overview(principal: AnalyticsViewer, resources: Resources) -> dict[str, Any]:
    return await AnalyticsService(resources.sessionmaker).overview(principal)


@analytics.get("/documents")
async def analytics_documents(principal: AnalyticsViewer, resources: Resources, days: Days = 30) -> dict[str, Any]:
    return await AnalyticsService(resources.sessionmaker).documents(principal, days)


@analytics.get("/retrieval")
async def analytics_retrieval(principal: AnalyticsViewer, resources: Resources, days: Days = 30) -> dict[str, Any]:
    return await AnalyticsService(resources.sessionmaker).retrieval(principal, days)


# --- embeddings ----------------------------------------------------------------------------

embeddings = APIRouter(prefix="/embeddings", tags=["embeddings"], responses=ERROR_RESPONSES)
OrgAdmin = Annotated[Principal, Depends(require_permission(Permission.ORGANIZATION_MANAGE))]


class ReindexRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["stale", "failed", "all"]


@embeddings.get("", summary="Embedding configurations and index statistics for this organization")
async def get_embeddings(principal: OrgAdmin, resources: Resources) -> dict[str, Any]:
    return await embedding_overview(resources.sessionmaker, resources.qdrant, principal.organization_id)


@embeddings.post("/reindex", status_code=status.HTTP_202_ACCEPTED, summary="Queue this organization's documents")
async def reindex(
    data: ReindexRequest, principal: OrgAdmin, resources: Resources, session: DbSession, meta: Meta
) -> dict[str, int]:
    queued = await queue_reindex(
        resources.sessionmaker, resources.job_queue, data.mode, principal.organization_id, principal.user_id
    )
    record_audit(
        session,
        action="embeddings.reindex",
        meta=meta,
        organization_id=principal.organization_id,
        actor_id=principal.user_id,
        details={"mode": data.mode, "queued": queued},
    )
    await session.commit()
    return {"queued": queued}
