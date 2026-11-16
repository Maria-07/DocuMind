"""Internal API for the n8n workflow (``/internal/...``).

* Not part of the public API: it is mounted outside ``/api``, so the website proxy (which only forwards
  ``/api/*``) and the edge proxy never route to it. n8n reaches it over the Docker network.
* Every call needs the shared secret in ``X-Internal-Api-Key`` (constant-time comparison). With
  ``N8N_ENABLED=false`` the whole router answers 404, as if it did not exist.
* Every call names the organization it acts for; ids belonging to another organization are "not found".
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response, status

from app.api.deps import Resources, SettingsDep
from app.core.errors import NotFoundError
from app.schemas.common import ERROR_RESPONSES
from app.schemas.workflow import (
    JobStatusRead,
    ProcessRequest,
    ProcessResult,
    RetryRequest,
    RetryResult,
    WorkflowStatusUpdate,
)
from app.services.workflow import INTERNAL_KEY_HEADER, WorkflowService, check_internal_key


async def require_workflow_caller(
    settings: SettingsDep, x_internal_api_key: Annotated[str | None, Header(alias=INTERNAL_KEY_HEADER)] = None
) -> None:
    if not settings.n8n_enabled:
        raise NotFoundError("Not found")
    check_internal_key(settings, x_internal_api_key)


def get_workflow_service(resources: Resources) -> WorkflowService:
    return WorkflowService(resources.sessionmaker, resources.settings, resources.job_queue)


WorkflowServiceDep = Annotated[WorkflowService, Depends(get_workflow_service)]

router = APIRouter(
    prefix="/internal",
    tags=["internal (n8n)"],
    dependencies=[Depends(require_workflow_caller)],
    responses={**ERROR_RESPONSES, 409: {"description": "Conflict: the job is not in a state that allows this"}},
)


@router.post(
    "/documents/{document_id}/process",
    response_model=ProcessResult,
    summary="Start processing an uploaded document (asynchronous; returns the job state at once)",
)
async def process_document(
    document_id: uuid.UUID, data: ProcessRequest, response: Response, service: WorkflowServiceDep
) -> ProcessResult:
    result = await service.process(data.organization_id, document_id, data.job_id, data.execution_id)
    response.status_code = status.HTTP_202_ACCEPTED if result.started else status.HTTP_200_OK
    return result


@router.get(
    "/jobs/{job_id}/status",
    response_model=JobStatusRead,
    summary="Job state, stage, retry budget and last error (the single source of truth for the workflow)",
)
async def job_status(
    job_id: uuid.UUID, organization_id: Annotated[uuid.UUID, Query()], service: WorkflowServiceDep
) -> JobStatusRead:
    return await service.status(organization_id, job_id)


@router.post(
    "/jobs/{job_id}/retry",
    response_model=RetryResult,
    summary="Re-queue a job that failed with a temporary error (idempotent, bounded by N8N_MAX_RETRIES)",
)
async def retry_job(
    job_id: uuid.UUID, data: RetryRequest, response: Response, service: WorkflowServiceDep
) -> RetryResult:
    result = await service.retry(data.organization_id, job_id, data.execution_id)
    response.status_code = status.HTTP_202_ACCEPTED if result.retried else status.HTTP_200_OK
    return result


@router.post(
    "/jobs/{job_id}/workflow-status",
    response_model=JobStatusRead,
    summary="Record the workflow's verdict (succeeded / permanent failure / retries exhausted)",
)
async def record_workflow_status(
    job_id: uuid.UUID, data: WorkflowStatusUpdate, service: WorkflowServiceDep
) -> JobStatusRead:
    return await service.record(
        data.organization_id, job_id, state=data.state, execution_id=data.execution_id, message=data.message
    )
