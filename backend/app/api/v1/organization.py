from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, DbSession, Meta, require_permission
from app.core.permissions import Permission, Principal
from app.models import User
from app.schemas.common import ERROR_RESPONSES
from app.schemas.organization import OrganizationRead, OrganizationUpdate
from app.schemas.user import MemberRead
from app.services.users import OrganizationService

router = APIRouter(prefix="/organization", tags=["organization"], responses=ERROR_RESPONSES)

OrgAdmin = Annotated[Principal, Depends(require_permission(Permission.ORGANIZATION_MANAGE))]


@router.get("", response_model=OrganizationRead, summary="The caller's organization")
async def get_organization(principal: CurrentPrincipal, session: DbSession) -> OrganizationRead:
    return OrganizationRead.model_validate(await OrganizationService(session).get(principal))


@router.patch("", response_model=OrganizationRead)
async def update_organization(
    data: OrganizationUpdate, principal: OrgAdmin, session: DbSession, meta: Meta
) -> OrganizationRead:
    return OrganizationRead.model_validate(await OrganizationService(session).update(principal, data, meta))


@router.get("/members", response_model=list[MemberRead], summary="Active members of the caller's organization")
async def list_members(principal: CurrentPrincipal, session: DbSession) -> list[MemberRead]:
    rows = await session.execute(
        select(User)
        .where(User.organization_id == principal.organization_id, User.is_active.is_(True))
        .order_by(User.full_name)
        .limit(1000)
    )
    return [MemberRead.model_validate(u) for u in rows.scalars()]
