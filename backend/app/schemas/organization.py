from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import DocumentVisibility
from app.schemas.common import ORMModel


class OrganizationSettings(BaseModel):
    """Typed view over organizations.settings (JSONB). Unknown keys are dropped."""

    model_config = ConfigDict(extra="ignore")

    users_can_upload: bool = False
    default_document_visibility: DocumentVisibility | None = None


class OrganizationRead(ORMModel):
    id: uuid.UUID
    name: str
    slug: str
    settings: OrganizationSettings
    storage_quota_bytes: int | None
    created_at: datetime


class OrganizationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=2, max_length=200)
    settings: OrganizationSettings | None = None
