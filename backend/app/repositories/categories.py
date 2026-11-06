from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category


class CategoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_in_org(self, organization_id: uuid.UUID) -> Sequence[Category]:
        stmt = select(Category).where(Category.organization_id == organization_id).order_by(Category.name)
        return (await self.session.execute(stmt)).scalars().all()

    async def get_in_org(self, organization_id: uuid.UUID, category_id: uuid.UUID) -> Category | None:
        stmt = select(Category).where(Category.id == category_id, Category.organization_id == organization_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def name_taken(self, organization_id: uuid.UUID, name: str, exclude_id: uuid.UUID | None = None) -> bool:
        stmt = select(func.count()).where(
            Category.organization_id == organization_id, func.lower(Category.name) == name.lower()
        )
        if exclude_id is not None:
            stmt = stmt.where(Category.id != exclude_id)
        return bool(await self.session.scalar(stmt))

    def add(self, category: Category) -> None:
        self.session.add(category)

    async def delete(self, category: Category) -> None:
        await self.session.delete(category)
