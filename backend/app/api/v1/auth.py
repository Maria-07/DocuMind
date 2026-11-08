from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import (
    CurrentPrincipal,
    DbSession,
    Meta,
    Resources,
    SettingsDep,
    rate_limit_by_ip,
    rate_limit_identifier,
)
from app.core.config import Settings
from app.repositories.users import OrganizationRepository, UserRepository
from app.schemas.auth import ChangePasswordRequest, LoginRequest, MeResponse, RegisterRequest, TokenResponse
from app.schemas.common import ERROR_RESPONSES
from app.schemas.organization import OrganizationRead
from app.schemas.user import UserRead
from app.services.auth import AuthService, IssuedTokens

router = APIRouter(prefix="/auth", tags=["auth"], responses=ERROR_RESPONSES)

# Strict budget for credential guessing (login / register).
_auth_rate_limit = [Depends(rate_limit_by_ip("auth", "rate_limit_auth_per_minute"))]
# Refresh runs on every page load and token expiry and is protected by an unguessable, rotating
# httpOnly cookie, so it has its own, larger budget (sharing the login budget logged users out).
_refresh_rate_limit = [Depends(rate_limit_by_ip("refresh", "rate_limit_refresh_per_minute"))]


def _cookie_path(settings: Settings) -> str:
    # The refresh cookie is only ever sent to the auth endpoints.
    return f"{settings.api_v1_prefix}/auth"


def _set_refresh_cookie(response: Response, issued: IssuedTokens, settings: Settings) -> None:
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=issued.refresh_token,
        expires=issued.refresh_expires_at,
        path=_cookie_path(settings),
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )


def _clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=_cookie_path(settings),
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )


def _token_response(issued: IssuedTokens) -> TokenResponse:
    return TokenResponse(
        access_token=issued.access_token, expires_in=issued.expires_in, user=UserRead.model_validate(issued.user)
    )


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=TokenResponse,
    dependencies=_auth_rate_limit,
    summary="Create an organization and its first (admin) user",
)
async def register(
    data: RegisterRequest, response: Response, session: DbSession, settings: SettingsDep, meta: Meta
) -> TokenResponse:
    issued = await AuthService(session, settings).register(data, meta)
    _set_refresh_cookie(response, issued, settings)
    return _token_response(issued)


@router.post("/login", response_model=TokenResponse, dependencies=_auth_rate_limit)
async def login(
    data: LoginRequest,
    response: Response,
    session: DbSession,
    settings: SettingsDep,
    meta: Meta,
    resources: Resources,
) -> TokenResponse:
    await rate_limit_identifier(resources, "login", str(data.email), "rate_limit_login_per_account_per_minute")
    issued = await AuthService(session, settings).login(data, meta)
    _set_refresh_cookie(response, issued, settings)
    return _token_response(issued)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    dependencies=_refresh_rate_limit,
    summary="Rotate the refresh-token cookie and issue a new access token",
)
async def refresh(
    request: Request, response: Response, session: DbSession, settings: SettingsDep, meta: Meta
) -> TokenResponse:
    raw = request.cookies.get(settings.refresh_cookie_name)
    issued = await AuthService(session, settings).refresh(raw, meta)
    _set_refresh_cookie(response, issued, settings)
    return _token_response(issued)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def logout(request: Request, session: DbSession, settings: SettingsDep, meta: Meta) -> Response:
    await AuthService(session, settings).logout(request.cookies.get(settings.refresh_cookie_name), meta)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(response, settings)
    return response


@router.get("/me", response_model=MeResponse)
async def me(principal: CurrentPrincipal, session: DbSession) -> MeResponse:
    user = await UserRepository(session).get_in_org(principal.organization_id, principal.user_id)
    org = await OrganizationRepository(session).get(principal.organization_id)
    assert user is not None and org is not None  # guaranteed by get_principal
    return MeResponse(
        user=UserRead.model_validate(user),
        organization=OrganizationRead.model_validate(org),
        permissions=sorted(principal.permissions),
    )


@router.post(
    "/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Change password; signs out all sessions",
)
async def change_password(
    data: ChangePasswordRequest, principal: CurrentPrincipal, session: DbSession, settings: SettingsDep, meta: Meta
) -> Response:
    await AuthService(session, settings).change_password(principal.user_id, principal.organization_id, data, meta)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(response, settings)
    return response
