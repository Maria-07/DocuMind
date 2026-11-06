from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import DbSession, Meta, SettingsDep, require_permission
from app.core.permissions import Permission, Principal
from app.models.enums import Role
from app.schemas.common import ERROR_RESPONSES, Page, PageParams
from app.schemas.user import UserCreate, UserRead, UserUpdate
from app.services.users import UserService

router = APIRouter(prefix="/users", tags=["users"], responses=ERROR_RESPONSES)

Admin = Annotated[Principal, Depends(require_permission(Permission.USERS_MANAGE))]


@router.get("", response_model=Page[UserRead])
async def list_users(
    principal: Admin,
    session: DbSession,
    settings: SettingsDep,
    page: Annotated[PageParams, Depends()],
    q: Annotated[str | None, Query(max_length=200)] = None,
    role: Role | None = None,
    is_active: bool | None = None,
) -> Page[UserRead]:
    users, total = await UserService(session, settings).list(principal, page, q=q, role=role, is_active=is_active)
    return Page(
        items=[UserRead.model_validate(u) for u in users], total=total, page=page.page, page_size=page.page_size
    )


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(
    data: UserCreate, principal: Admin, session: DbSession, settings: SettingsDep, meta: Meta
) -> UserRead:
    return UserRead.model_validate(await UserService(session, settings).create(principal, data, meta))


@router.get("/{user_id}", response_model=UserRead)
async def get_user(user_id: uuid.UUID, principal: Admin, session: DbSession, settings: SettingsDep) -> UserRead:
    return UserRead.model_validate(await UserService(session, settings).get(principal, user_id))


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: uuid.UUID, data: UserUpdate, principal: Admin, session: DbSession, settings: SettingsDep, meta: Meta
) -> UserRead:
    return UserRead.model_validate(await UserService(session, settings).update(principal, user_id, data, meta))


@router.delete("/{user_id}", response_model=UserRead, summary="Deactivate a user (accounts are never hard-deleted)")
async def deactivate_user(
    user_id: uuid.UUID, principal: Admin, session: DbSession, settings: SettingsDep, meta: Meta
) -> UserRead:
    user = await UserService(session, settings).update(principal, user_id, UserUpdate(is_active=False), meta)
    return UserRead.model_validate(user)
