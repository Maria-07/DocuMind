from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.chat import Scope


class LinkRead(BaseModel):
    """A hyperlink found in a document. ``url`` is exactly the target written in the file."""

    id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    file_type: str
    url: str
    domain: str | None
    link_type: Literal["hyperlink", "url", "email"] = Field(
        description="hyperlink: target hidden behind anchor text · url: written out as text · email: mailto link"
    )
    anchor_text: str | None
    page_number: int | None
    sheet_name: str | None
    cell_reference: str | None
    section_name: str | None
    location: str = Field(description='Human-readable location, e.g. "page 2" or "sheet People, cell B2"')
    created_at: datetime


class DocumentLinksResponse(BaseModel):
    document_id: uuid.UUID
    index_version: int
    items: list[LinkRead]
    total: int


class LinkSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        min_length=1,
        max_length=500,
        description='A question or keywords: "GitHub", "linkedin.com", "portfolio", "all links"',
    )
    filters: Scope | None = None
    limit: int = Field(default=50, ge=1, le=200)


class LinkSearchResponse(BaseModel):
    results: list[LinkRead]
    took_ms: float
