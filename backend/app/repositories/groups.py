from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DocumentPermission, GroupMember, UserGroup
from app.models.enums import PrincipalType


class GroupRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_in_org(self, organization_id: uuid.UUID) -> Sequence[UserGroup]:
        stmt = select(UserGroup).where(UserGroup.organization_id == organization_id).order_by(UserGroup.name)
        return (await self.session.execute(stmt)).scalars().all()

    async def get_in_org(self, organization_id: uuid.UUID, group_id: uuid.UUID) -> UserGroup | None:
        stmt = select(UserGroup).where(UserGroup.id == group_id, UserGroup.organization_id == organization_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def name_taken(self, organization_id: uuid.UUID, name: str, exclude_id: uuid.UUID | None = None) -> bool:
        stmt = select(func.count()).where(
            UserGroup.organization_id == organization_id, func.lower(UserGroup.name) == name.lower()
        )
        if exclude_id is not None:
            stmt = stmt.where(UserGroup.id != exclude_id)
        return bool(await self.session.scalar(stmt))

    async def ids_existing_in_org(self, organization_id: uuid.UUID, group_ids: Sequence[uuid.UUID]) -> set[uuid.UUID]:
        if not group_ids:
            return set()
        stmt = select(UserGroup.id).where(UserGroup.organization_id == organization_id, UserGroup.id.in_(group_ids))
        return set((await self.session.execute(stmt)).scalars().all())

    async def member_ids(self, group_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, list[uuid.UUID]]:
        members: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
        if not group_ids:
            return members
        stmt = select(GroupMember.group_id, GroupMember.user_id).where(GroupMember.group_id.in_(group_ids))
        for group_id, user_id in (await self.session.execute(stmt)).all():
            members[group_id].append(user_id)
        return members

    async def set_members(self, group_id: uuid.UUID, user_ids: Sequence[uuid.UUID]) -> None:
        await self.session.execute(delete(GroupMember).where(GroupMember.group_id == group_id))
        self.session.add_all(GroupMember(group_id=group_id, user_id=uid) for uid in dict.fromkeys(user_ids))

    async def delete(self, group: UserGroup) -> None:
        # Grants reference principals polymorphically (no FK), so clean them up explicitly.
        await self.session.execute(
            delete(DocumentPermission).where(
                DocumentPermission.principal_type == PrincipalType.GROUP,
                DocumentPermission.principal_id == group.id,
            )
        )
        await self.session.delete(group)

    def add(self, group: UserGroup) -> None:
        self.session.add(group)
