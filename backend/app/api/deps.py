"""FastAPI dependencies: DB session, authenticated principal, permission checks, rate limits."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AuthenticationError, PermissionDeniedError, RateLimitedError
from app.core.permissions import Permission, Principal, permissions_for
from app.core.resources import AppResources
from app.core.security import decode_access_token
from app.repositories.users import UserRepository
from app.schemas.organization import OrganizationSettings
from app.services.audit import RequestMeta
from app.services.documents import DocumentService

_bearer = HTTPBearer(auto_error=False, description="Access token from /auth/login or /auth/refresh")


def get_resources(request: Request) -> AppResources:
    resources: AppResources = request.app.state.resources
    return resources


Resources = Annotated[AppResources, Depends(get_resources)]


def get_settings(resources: Resources) -> Settings:
    return resources.settings


SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_db(resources: Resources) -> AsyncIterator[AsyncSession]:
    # Uncommitted work is rolled back when the session closes (e.g. on an exception).
    async with resources.sessionmaker() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db)]


def get_request_meta(request: Request) -> RequestMeta:
    return RequestMeta(
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


Meta = Annotated[RequestMeta, Depends(get_request_meta)]


async def get_principal(
    session: DbSession,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if credentials is None:
        raise AuthenticationError("Not authenticated")
    claims = decode_access_token(credentials.credentials, settings)

    # Role, active flag and org membership come from the DB, not the token: changes apply immediately.
    users = UserRepository(session)
    found = await users.get_with_organization(claims.user_id)
    if found is None:
        raise AuthenticationError("Not authenticated", code="invalid_token")
    user, org = found
    if not user.is_active or user.organization_id != claims.organization_id:
        raise AuthenticationError("Not authenticated", code="invalid_token")

    org_settings = OrganizationSettings.model_validate(org.settings)
    return Principal(
        user_id=user.id,
        organization_id=org.id,
        role=user.role,
        email=user.email,
        group_ids=await users.group_ids_for(user.id),
        permissions=permissions_for(user.role, users_can_upload=org_settings.users_can_upload),
    )


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def require_permission(*required: Permission) -> Callable[..., Awaitable[Principal]]:
    async def dependency(principal: CurrentPrincipal) -> Principal:
        missing = [p for p in required if not principal.has(p)]
        if missing:
            raise PermissionDeniedError(
                "You don't have permission to perform this action",
                details={"missing_permissions": [m.value for m in missing]},
            )
        return principal

    return dependency


def _rate_limited(retry_after: int) -> RateLimitedError:
    return RateLimitedError("Too many requests, please slow down", headers={"Retry-After": str(max(retry_after, 1))})


def rate_limit_by_ip(scope: str, limit_setting: str) -> Callable[..., Awaitable[None]]:
    async def dependency(request: Request, resources: Resources) -> None:
        ip = request.client.host if request.client else "unknown"
        limit = int(getattr(resources.settings, limit_setting))
        result = await resources.rate_limiter.hit(f"{scope}:ip:{ip}", limit)
        if not result.allowed:
            raise _rate_limited(result.reset_in_seconds)

    return dependency


def rate_limit_by_user(scope: str, limit_setting: str) -> Callable[..., Awaitable[None]]:
    async def dependency(principal: CurrentPrincipal, resources: Resources) -> None:
        limit = int(getattr(resources.settings, limit_setting))
        result = await resources.rate_limiter.hit(f"{scope}:user:{principal.user_id}", limit)
        if not result.allowed:
            raise _rate_limited(result.reset_in_seconds)

    return dependency


def get_document_service(session: DbSession, resources: Resources) -> DocumentService:
    return DocumentService(session, resources.settings, resources.storage, resources.job_queue, resources.vector_store)


DocumentServiceDep = Annotated[DocumentService, Depends(get_document_service)]
