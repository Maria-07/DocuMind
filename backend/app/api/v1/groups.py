from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import CurrentPrincipal, DbSession, Meta, require_permission
from app.core.permissions import Permission, Principal
from app.schemas.common import ERROR_RESPONSES
from app.schemas.group import GroupCreate, GroupMembersUpdate, GroupRead, GroupUpdate
from app.services.groups import GroupService

router = APIRouter(prefix="/groups", tags=["groups"], responses=ERROR_RESPONSES)

GroupAdmin = Annotated[Principal, Depends(require_permission(Permission.GROUPS_MANAGE))]


@router.get("", response_model=list[GroupRead], summary="List groups (any member; used by share dialogs)")
async def list_groups(principal: CurrentPrincipal, session: DbSession) -> list[GroupRead]:
    return await GroupService(session).list(principal)


@router.post("", response_model=GroupRead, status_code=status.HTTP_201_CREATED)
async def create_group(data: GroupCreate, principal: GroupAdmin, session: DbSession, meta: Meta) -> GroupRead:
    return await GroupService(session).create(principal, data, meta)


@router.get("/{group_id}", response_model=GroupRead)
async def get_group(group_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession) -> GroupRead:
    return await GroupService(session).get(principal, group_id)


@router.patch("/{group_id}", response_model=GroupRead)
async def update_group(
    group_id: uuid.UUID, data: GroupUpdate, principal: GroupAdmin, session: DbSession, meta: Meta
) -> GroupRead:
    return await GroupService(session).update(principal, group_id, data, meta)


@router.put("/{group_id}/members", response_model=GroupRead)
async def set_group_members(
    group_id: uuid.UUID, data: GroupMembersUpdate, principal: GroupAdmin, session: DbSession, meta: Meta
) -> GroupRead:
    return await GroupService(session).set_members(principal, group_id, data.user_ids, meta)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_group(group_id: uuid.UUID, principal: GroupAdmin, session: DbSession, meta: Meta) -> Response:
    await GroupService(session).delete(principal, group_id, meta)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
