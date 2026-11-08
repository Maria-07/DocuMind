from __future__ import annotations

import builtins
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.core.permissions import Principal
from app.models import UserGroup
from app.repositories.groups import GroupRepository
from app.repositories.users import UserRepository
from app.schemas.group import GroupCreate, GroupRead, GroupUpdate
from app.services.audit import RequestMeta, record_audit


class GroupService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.groups = GroupRepository(session)
        self.users = UserRepository(session)

    async def _read(self, groups: builtins.list[UserGroup]) -> builtins.list[GroupRead]:
        members = await self.groups.member_ids([g.id for g in groups])
        return [GroupRead.model_validate(g).model_copy(update={"member_ids": members.get(g.id, [])}) for g in groups]

    async def list(self, principal: Principal) -> builtins.list[GroupRead]:
        return await self._read(list(await self.groups.list_in_org(principal.organization_id)))

    async def get(self, principal: Principal, group_id: uuid.UUID) -> GroupRead:
        return (await self._read([await self._get(principal, group_id)]))[0]

    async def _get(self, principal: Principal, group_id: uuid.UUID) -> UserGroup:
        group = await self.groups.get_in_org(principal.organization_id, group_id)
        if group is None:
            raise NotFoundError("Group not found", code="group_not_found")
        return group

    async def create(self, principal: Principal, data: GroupCreate, meta: RequestMeta) -> GroupRead:
        if await self.groups.name_taken(principal.organization_id, data.name):
            raise ConflictError("A group with this name already exists", code="group_name_taken")
        group = UserGroup(
            organization_id=principal.organization_id, name=data.name.strip(), description=data.description
        )
        self.groups.add(group)
        await self.session.flush()
        record_audit(
            self.session,
            action="group.created",
            meta=meta,
            organization_id=principal.organization_id,
            actor_id=principal.user_id,
            resource_type="group",
            resource_id=group.id,
        )
        await self.session.commit()
        return (await self._read([group]))[0]

    async def update(
        self, principal: Principal, group_id: uuid.UUID, data: GroupUpdate, meta: RequestMeta
    ) -> GroupRead:
        group = await self._get(principal, group_id)
        changes = data.model_dump(exclude_unset=True)
        if changes.get("name"):
            if await self.groups.name_taken(principal.organization_id, changes["name"], exclude_id=group.id):
                raise ConflictError("A group with this name already exists", code="group_name_taken")
            group.name = changes["name"].strip()
        if "description" in changes:
            group.description = changes["description"]
        record_audit(
            self.session,
            action="group.updated",
            meta=meta,
            organization_id=principal.organization_id,
            actor_id=principal.user_id,
            resource_type="group",
            resource_id=group.id,
        )
        await self.session.commit()
        return (await self._read([group]))[0]

    async def set_members(
        self, principal: Principal, group_id: uuid.UUID, user_ids: builtins.list[uuid.UUID], meta: RequestMeta
    ) -> GroupRead:
        group = await self._get(principal, group_id)
        existing = await self.users.ids_existing_in_org(principal.organization_id, user_ids)
        unknown = [str(uid) for uid in user_ids if uid not in existing]
        if unknown:
            raise ValidationFailedError(
                "Some users don't exist in this organization", code="unknown_users", details={"user_ids": unknown}
            )
        await self.groups.set_members(group.id, user_ids)
        record_audit(
            self.session,
            action="group.members_changed",
            meta=meta,
            organization_id=principal.organization_id,
            actor_id=principal.user_id,
            resource_type="group",
            resource_id=group.id,
            details={"member_count": len(set(user_ids))},
        )
        await self.session.commit()
        return (await self._read([group]))[0]

    async def delete(self, principal: Principal, group_id: uuid.UUID, meta: RequestMeta) -> None:
        group = await self._get(principal, group_id)
        await self.groups.delete(group)
        record_audit(
            self.session,
            action="group.deleted",
            meta=meta,
            organization_id=principal.organization_id,
            actor_id=principal.user_id,
            resource_type="group",
            resource_id=group_id,
        )
        await self.session.commit()
