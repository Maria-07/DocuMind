from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.core.permissions import Principal
from app.models import Category
from app.repositories.categories import CategoryRepository
from app.schemas.category import CategoryCreate, CategoryUpdate
from app.services.audit import RequestMeta, record_audit


class CategoryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.categories = CategoryRepository(session)

    async def list(self, principal: Principal) -> Sequence[Category]:
        return await self.categories.list_in_org(principal.organization_id)

    async def get(self, principal: Principal, category_id: uuid.UUID) -> Category:
        category = await self.categories.get_in_org(principal.organization_id, category_id)
        if category is None:
            raise NotFoundError("Category not found", code="category_not_found")
        return category

    async def create(self, principal: Principal, data: CategoryCreate, meta: RequestMeta) -> Category:
        if await self.categories.name_taken(principal.organization_id, data.name):
            raise ConflictError("A category with this name already exists", code="category_name_taken")
        category = Category(organization_id=principal.organization_id, name=data.name.strip(), color=data.color)
        self.categories.add(category)
        await self.session.flush()
        record_audit(
            self.session,
            action="category.created",
            meta=meta,
            organization_id=principal.organization_id,
            actor_id=principal.user_id,
            resource_type="category",
            resource_id=category.id,
        )
        await self.session.commit()
        return category

    async def update(
        self, principal: Principal, category_id: uuid.UUID, data: CategoryUpdate, meta: RequestMeta
    ) -> Category:
        category = await self.get(principal, category_id)
        changes = data.model_dump(exclude_unset=True)
        if changes.get("name"):
            if await self.categories.name_taken(principal.organization_id, changes["name"], exclude_id=category.id):
                raise ConflictError("A category with this name already exists", code="category_name_taken")
            category.name = changes["name"].strip()
        if "color" in changes:
            category.color = changes["color"]
        await self.session.commit()
        return category

    async def delete(self, principal: Principal, category_id: uuid.UUID, meta: RequestMeta) -> None:
        category = await self.get(principal, category_id)
        await self.categories.delete(category)  # documents.category_id → NULL (FK ON DELETE SET NULL)
        record_audit(
            self.session,
            action="category.deleted",
            meta=meta,
            organization_id=principal.organization_id,
            actor_id=principal.user_id,
            resource_type="category",
            resource_id=category_id,
        )
        await self.session.commit()
