from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import ORMModel

_HEX_COLOR = r"^#[0-9a-fA-F]{6}$"


class CategoryRead(ORMModel):
    id: uuid.UUID
    name: str
    color: str | None
    created_at: datetime


class CategoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    color: str | None = Field(default=None, pattern=_HEX_COLOR)


class CategoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    color: str | None = Field(default=None, pattern=_HEX_COLOR)
