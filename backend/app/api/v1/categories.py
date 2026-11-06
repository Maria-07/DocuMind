from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import CurrentPrincipal, DbSession, Meta, require_permission
from app.core.permissions import Permission, Principal
from app.schemas.category import CategoryCreate, CategoryRead, CategoryUpdate
from app.schemas.common import ERROR_RESPONSES
from app.services.categories import CategoryService

router = APIRouter(prefix="/categories", tags=["categories"], responses=ERROR_RESPONSES)

CategoryAdmin = Annotated[Principal, Depends(require_permission(Permission.CATEGORIES_MANAGE))]


@router.get("", response_model=list[CategoryRead])
async def list_categories(principal: CurrentPrincipal, session: DbSession) -> list[CategoryRead]:
    return [CategoryRead.model_validate(c) for c in await CategoryService(session).list(principal)]


@router.post("", response_model=CategoryRead, status_code=status.HTTP_201_CREATED)
async def create_category(
    data: CategoryCreate, principal: CategoryAdmin, session: DbSession, meta: Meta
) -> CategoryRead:
    return CategoryRead.model_validate(await CategoryService(session).create(principal, data, meta))


@router.patch("/{category_id}", response_model=CategoryRead)
async def update_category(
    category_id: uuid.UUID, data: CategoryUpdate, principal: CategoryAdmin, session: DbSession, meta: Meta
) -> CategoryRead:
    return CategoryRead.model_validate(await CategoryService(session).update(principal, category_id, data, meta))


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_category(category_id: uuid.UUID, principal: CategoryAdmin, session: DbSession, meta: Meta) -> Response:
    await CategoryService(session).delete(principal, category_id, meta)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
