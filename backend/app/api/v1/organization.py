from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import CurrentPrincipal, DbSession, Meta, require_permission
from app.core.permissions import Permission, Principal
from app.schemas.common import ERROR_RESPONSES
from app.schemas.organization import OrganizationRead, OrganizationUpdate
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
