from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.enums import Role
from app.schemas.common import ORMModel


class UserRead(ORMModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    email: str
    full_name: str
    role: Role
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime


class MemberRead(ORMModel):
    """Directory entry visible to every member (for share dialogs) — no role or login data."""

    id: uuid.UUID
    full_name: str
    email: str


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    role: Role = Role.USER
    password: str = Field(min_length=8, max_length=128, description="Initial password; share it securely.")


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    role: Role | None = None
    is_active: bool | None = None
