from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    DocumentStatus,
    DocumentVisibility,
    FileType,
    GrantPermission,
    JobStatus,
    PrincipalType,
)
from app.schemas.common import ErrorInfo, ORMModel


class IngestionJobRead(ORMModel):
    id: uuid.UUID
    index_version: int | None
    status: JobStatus
    attempt: int
    stage: str | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class DocumentRead(ORMModel):
    id: uuid.UUID
    title: str
    original_filename: str
    file_type: FileType
    mime_type: str
    size_bytes: int
    status: DocumentStatus
    visibility: DocumentVisibility
    category_id: uuid.UUID | None
    owner_id: uuid.UUID | None
    page_count: int | None
    sheet_names: list[str] | None
    chunk_count: int
    metadata: dict[str, Any] = Field(validation_alias="doc_metadata")
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    indexed_at: datetime | None
    can_manage: bool = False


class DocumentDetail(DocumentRead):
    sha256: str
    latest_job: IngestionJobRead | None = None


class DocumentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=300)
    category_id: uuid.UUID | None = None
    visibility: DocumentVisibility | None = None


class UploadResult(BaseModel):
    filename: str
    status: Literal["accepted", "duplicate", "rejected"]
    document: DocumentRead | None = None
    job_id: uuid.UUID | None = None
    error: ErrorInfo | None = None


class UploadResponse(BaseModel):
    results: list[UploadResult]
    accepted: int
    duplicates: int
    rejected: int


class GrantInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal_type: PrincipalType
    principal_id: uuid.UUID
    permission: GrantPermission = GrantPermission.VIEW


class GrantRead(ORMModel):
    principal_type: PrincipalType
    principal_id: uuid.UUID
    permission: GrantPermission
    granted_by: uuid.UUID | None
    created_at: datetime


class PermissionsRead(BaseModel):
    visibility: DocumentVisibility
    owner_id: uuid.UUID | None
    grants: list[GrantRead]


class PermissionsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grants: list[GrantInput] = Field(default_factory=list, max_length=500)


class DocumentListFilters(BaseModel):
    q: str | None = Field(default=None, max_length=200)
    status: DocumentStatus | None = None
    category_id: uuid.UUID | None = None
    file_type: FileType | None = None
    owner_id: uuid.UUID | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None
    sort: Literal["created_at", "-created_at", "title", "-title", "size_bytes", "-size_bytes"] = "-created_at"
