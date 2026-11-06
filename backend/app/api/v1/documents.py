from __future__ import annotations

import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile, status
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentPrincipal, DocumentServiceDep, Meta, rate_limit_by_user
from app.models.enums import DocumentVisibility
from app.schemas.common import ERROR_RESPONSES, Page, PageParams
from app.schemas.document import (
    DocumentDetail,
    DocumentListFilters,
    DocumentRead,
    DocumentUpdate,
    GrantRead,
    IngestionJobRead,
    PermissionsRead,
    PermissionsUpdate,
    UploadResponse,
)
from app.services.documents import DownloadHandle

router = APIRouter(prefix="/documents", tags=["documents"], responses=ERROR_RESPONSES)


@router.post(
    "/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit_by_user("upload", "rate_limit_upload_per_minute"))],
    summary="Upload one or more PDF/DOCX/XLSX/XLS files",
    description=(
        "Each file is validated (extension + content sniffing + size), de-duplicated by SHA-256 within the "
        "organization, stored, and queued for background indexing. Results are reported per file. "
        "Returns 202 if at least one file was accepted, otherwise 200 (all duplicates/rejected)."
    ),
)
async def upload_documents(
    principal: CurrentPrincipal,
    service: DocumentServiceDep,
    meta: Meta,
    response: Response,
    files: Annotated[list[UploadFile], File(description="Files to upload")],
    category_id: Annotated[uuid.UUID | None, Form()] = None,
    visibility: Annotated[DocumentVisibility | None, Form()] = None,
) -> UploadResponse:
    try:
        result = await service.upload_many(principal, files, category_id=category_id, visibility=visibility, meta=meta)
    finally:
        for f in files:
            await f.close()
    if result.accepted == 0:
        response.status_code = status.HTTP_200_OK
    return result


@router.get("", response_model=Page[DocumentRead], summary="List documents the caller can see")
async def list_documents(
    principal: CurrentPrincipal,
    service: DocumentServiceDep,
    page: Annotated[PageParams, Depends()],
    filters: Annotated[DocumentListFilters, Depends()],
) -> Page[DocumentRead]:
    items, total = await service.list(principal, filters, page)
    return Page(items=items, total=total, page=page.page, page_size=page.page_size)


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(
    document_id: uuid.UUID, principal: CurrentPrincipal, service: DocumentServiceDep
) -> DocumentDetail:
    return await service.get_detail(principal, document_id)


@router.patch("/{document_id}", response_model=DocumentRead)
async def update_document(
    document_id: uuid.UUID, data: DocumentUpdate, principal: CurrentPrincipal, service: DocumentServiceDep, meta: Meta
) -> DocumentRead:
    return await service.update(principal, document_id, data, meta)


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_202_ACCEPTED,
    response_class=Response,
    summary="Delete a document (immediately hidden; file and vectors purged in the background)",
)
async def delete_document(
    document_id: uuid.UUID, principal: CurrentPrincipal, service: DocumentServiceDep, meta: Meta
) -> Response:
    await service.delete(principal, document_id, meta)
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post("/{document_id}/reprocess", response_model=IngestionJobRead, status_code=status.HTTP_202_ACCEPTED)
async def reprocess_document(
    document_id: uuid.UUID, principal: CurrentPrincipal, service: DocumentServiceDep, meta: Meta
) -> IngestionJobRead:
    return await service.reprocess(principal, document_id, meta)


@router.get("/{document_id}/jobs", response_model=list[IngestionJobRead])
async def list_document_jobs(
    document_id: uuid.UUID, principal: CurrentPrincipal, service: DocumentServiceDep
) -> list[IngestionJobRead]:
    return await service.list_jobs(principal, document_id)


def _content_disposition(kind: str, filename: str) -> str:
    ascii_fallback = filename.encode("ascii", "ignore").decode("ascii").replace('"', "") or "document"
    return f"{kind}; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"


def _stream(handle: DownloadHandle, kind: str) -> StreamingResponse:
    doc = handle.document
    return StreamingResponse(
        handle.stream,
        media_type=doc.mime_type,
        headers={
            "Content-Disposition": _content_disposition(kind, doc.original_filename),
            "Content-Length": str(doc.size_bytes),
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/{document_id}/download", response_class=StreamingResponse, summary="Download the original file")
async def download_document(
    document_id: uuid.UUID, principal: CurrentPrincipal, service: DocumentServiceDep, meta: Meta
) -> StreamingResponse:
    return _stream(await service.open_download(principal, document_id, meta), "attachment")


@router.get(
    "/{document_id}/preview",
    response_class=StreamingResponse,
    summary="Inline preview (PDF). DOCX/Excel previews use extracted text once indexed.",
)
async def preview_document(
    document_id: uuid.UUID, principal: CurrentPrincipal, service: DocumentServiceDep, meta: Meta
) -> StreamingResponse:
    return _stream(await service.open_download(principal, document_id, meta, inline=True), "inline")


@router.get("/{document_id}/permissions", response_model=PermissionsRead)
async def get_document_permissions(
    document_id: uuid.UUID, principal: CurrentPrincipal, service: DocumentServiceDep
) -> PermissionsRead:
    document, grants = await service.get_permissions(principal, document_id)
    return PermissionsRead(
        visibility=document.visibility,
        owner_id=document.owner_id,
        grants=[GrantRead.model_validate(g) for g in grants],
    )


@router.put(
    "/{document_id}/permissions",
    response_model=PermissionsRead,
    summary="Replace the document's user/group grants",
    description=(
        "View grants take effect when visibility is 'restricted'; manage grants apply to 'restricted' and "
        "'organization' documents. Grants are ignored for 'private' documents."
    ),
)
async def replace_document_permissions(
    document_id: uuid.UUID,
    data: PermissionsUpdate,
    principal: CurrentPrincipal,
    service: DocumentServiceDep,
    meta: Meta,
) -> PermissionsRead:
    document, grants = await service.replace_permissions(principal, document_id, data.grants, meta)
    return PermissionsRead(
        visibility=document.visibility,
        owner_id=document.owner_id,
        grants=[GrantRead.model_validate(g) for g in grants],
    )
