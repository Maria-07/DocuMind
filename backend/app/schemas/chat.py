from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import FileType
from app.schemas.common import ORMModel


class Scope(BaseModel):
    """Restrict retrieval to some documents/categories/types (always within the user's permissions)."""

    model_config = ConfigDict(extra="forbid")

    document_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    category_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    file_types: list[FileType] = Field(default_factory=list, max_length=4)

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_defaults=True)


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)
    scope: Scope | None = None


class ConversationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    scope: Scope | None = None


class ConversationRead(ORMModel):
    id: uuid.UUID
    title: str
    scope: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class CitationRead(BaseModel):
    index: int
    chunk_id: uuid.UUID | None
    document_id: uuid.UUID | None
    document_title: str
    page_start: int | None = None
    page_end: int | None = None
    sheet_name: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    section_path: str | None = None
    snippet: str
    score: float | None = None
    url: str | None = Field(default=None, description="Frontend link to the cited location")


class MessageRead(ORMModel):
    id: uuid.UUID
    role: str
    content: str
    status: str
    grounded: bool
    created_at: datetime
    model: str | None = None
    route: str | None = Field(default=None, description='"chat" (no documents needed) or "documents"')
    latency_ms: int | None = None
    usage: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Token usage and estimated cost of this reply: input_tokens, output_tokens, reasoning_tokens, "
            "cache_read_input_tokens, cost_usd (null when the model's price is unknown) and a per-step "
            "breakdown (routing, answer)."
        ),
    )
    edit_of: uuid.UUID | None = Field(
        default=None, description="On a user message: the earlier message it replaced when the user edited it"
    )
    citations: list[CitationRead] = []


class ConversationDetail(ConversationRead):
    messages: list[MessageRead]


class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=4000)
    scope: Scope | None = Field(default=None, description="Overrides the conversation scope for this message")
    model: str | None = Field(
        default=None,
        max_length=100,
        description="Model id from GET /chat/models. Omit for the default model.",
    )
    edit_message_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Edit and resubmit: the id of one of your earlier messages in this conversation. That message and "
            "everything after it are hidden from the conversation (kept for the record) and this message "
            "takes its place."
        ),
    )


class SourceRead(BaseModel):
    """A retrieved chunk given to the model (shown as a source card)."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    location: str
    page_start: int | None = None
    sheet_name: str | None = None
    section_path: str | None = None
    snippet: str
    relevance: float | None = None


class AnswerResponse(BaseModel):
    """Non-streaming answer (``Accept: application/json``)."""

    conversation_id: uuid.UUID
    message: MessageRead
    sources: list[SourceRead]


class ModelPriceRead(BaseModel):
    """USD per 1M tokens, in force today."""

    input: float
    output: float
    cached_input: float | None = None


class ChatModelRead(BaseModel):
    id: str
    label: str
    provider: str
    description: str = ""
    is_default: bool
    unavailable_reason: str | None = Field(
        default=None, description="Set while the model's quota or usage limit is exhausted (shown greyed out)"
    )
    price: ModelPriceRead | None = Field(default=None, description="Null when the price is unknown (no cost shown)")


class ChatModelsResponse(BaseModel):
    default: str | None
    models: list[ChatModelRead]
