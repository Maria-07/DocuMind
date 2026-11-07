from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.chat import Scope


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=10, ge=1, le=50)
    filters: Scope | None = None
    rerank: bool = True


class SearchHit(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    file_type: str
    kind: str
    text: str
    location: str
    page_start: int | None = None
    page_end: int | None = None
    sheet_name: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    section_path: str | None = None
    score: float = Field(description="Fusion (hybrid) or cosine (semantic) score")
    relevance: float | None = Field(default=None, description="Cross-encoder relevance in [0, 1]")


class SearchResponse(BaseModel):
    mode: str
    results: list[SearchHit]
    candidate_count: int
    took_ms: float


class DocumentSearchHit(BaseModel):
    document_id: uuid.UUID
    document_title: str
    file_type: str
    best_relevance: float | None
    matching_chunks: int
    snippet: str
    location: str


class DocumentSearchResponse(BaseModel):
    results: list[DocumentSearchHit]
    took_ms: float
