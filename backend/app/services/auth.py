"""Registration, login (with lockout), refresh-token rotation with reuse detection, logout."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AuthenticationError, ConflictError, PermissionDeniedError, ValidationFailedError
from app.core.security import (
    burn_password_check,
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_token,
    password_problems,
    verify_password,
)
from app.models import Organization, RefreshToken, User
from app.models.enums import Role
from app.repositories.users import OrganizationRepository, RefreshTokenRepository, UserRepository
from app.schemas.auth import ChangePasswordRequest, LoginRequest, RegisterRequest
from app.services.audit import RequestMeta, record_audit
from app.utils.ids import uuid7
from app.utils.text import slugify

log = structlog.get_logger(__name__)

_INVALID_CREDENTIALS = "Invalid e-mail or password, or the account is temporarily locked"


@dataclass(frozen=True, slots=True)
class IssuedTokens:
    access_token: str
    expires_in: int
    refresh_token: str
    refresh_expires_at: datetime
    user: User


def _now() -> datetime:
    return datetime.now(UTC)


def ensure_password_strength(password: str, settings: Settings) -> None:
    problems = password_problems(password, settings.password_min_length)
    if problems:
        raise ValidationFailedError(
            "Password does not meet requirements", code="weak_password", details={"problems": problems}
        )


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.users = UserRepository(session)
        self.orgs = OrganizationRepository(session)
        self.tokens = RefreshTokenRepository(session)

    # --- registration ----------------------------------------------------------------------
    async def register(self, data: RegisterRequest, meta: RequestMeta) -> IssuedTokens:
        """Public sign-up (can be switched off with REGISTRATION_ENABLED=false)."""
        if not self.settings.registration_enabled:
            raise PermissionDeniedError(
                "Sign-up is closed. Ask an administrator for an account.", code="registration_disabled"
            )
        return await self.create_workspace(data, meta)

    async def create_workspace(self, data: RegisterRequest, meta: RequestMeta) -> IssuedTokens:
        """Create an organization and its first admin. Also the operator path (``app.cli create-admin``),
        which works while public sign-up is closed."""
        ensure_password_strength(data.password, self.settings)
        email = data.email.lower()
        if await self.users.get_by_email(email) is not None:
            raise ConflictError("An account with this e-mail already exists", code="email_taken")

        org = Organization(name=data.organization_name.strip(), slug=await self._unique_slug(data.organization_name))
        self.orgs.add(org)
        await self.session.flush()

        user = User(
            organization_id=org.id,
            email=email,
            password_hash=hash_password(data.password),
            full_name=data.full_name.strip(),
            role=Role.ADMIN,  # the person creating an organization administers it
            is_active=True,
            failed_login_attempts=0,
            last_login_at=_now(),
        )
        self.users.add(user)
        await self.session.flush()

        record_audit(
            self.session,
            action="organization.created",
            meta=meta,
            organization_id=org.id,
            actor_id=user.id,
            resource_type="organization",
            resource_id=org.id,
        )
        record_audit(
            self.session,
            action="auth.register",
            meta=meta,
            organization_id=org.id,
            actor_id=user.id,
            resource_type="user",
            resource_id=user.id,
        )
        issued = await self._issue_tokens(user, meta, family_id=None)
        try:
            await self.session.commit()
        except IntegrityError as exc:  # concurrent registration with the same e-mail
            await self.session.rollback()
            raise ConflictError("An account with this e-mail already exists", code="email_taken") from exc
        return issued

    async def _unique_slug(self, name: str) -> str:
        base = slugify(name)
        candidate = base
        for _ in range(5):
            if not await self.orgs.slug_exists(candidate):
                return candidate
            candidate = f"{base}-{secrets.token_hex(3)}"
        return f"{base}-{uuid.uuid4().hex[:12]}"

    # --- login -----------------------------------------------------------------------------
    async def login(self, data: LoginRequest, meta: RequestMeta) -> IssuedTokens:
        email = data.email.lower()
        user = await self.users.get_by_email(email)
        now = _now()

        if user is None:
            burn_password_check(data.password)
            record_audit(
                self.session,
                action="auth.login_failed",
                meta=meta,
                organization_id=None,
                actor_id=None,
                details={"reason": "unknown_email"},
            )
            await self.session.commit()
            raise AuthenticationError(_INVALID_CREDENTIALS, code="invalid_credentials")

        if user.locked_until is not None and user.locked_until > now:
            burn_password_check(data.password)
            record_audit(
                self.session,
                action="auth.login_failed",
                meta=meta,
                organization_id=user.organization_id,
                actor_id=user.id,
                details={"reason": "locked"},
            )
            await self.session.commit()
            raise AuthenticationError(_INVALID_CREDENTIALS, code="invalid_credentials")

        if not verify_password(data.password, user.password_hash):
            user.failed_login_attempts += 1
            details: dict[str, object] = {"reason": "bad_password", "attempts": user.failed_login_attempts}
            if user.failed_login_attempts >= self.settings.login_max_failed_attempts:
                user.locked_until = now + timedelta(minutes=self.settings.login_lockout_minutes)
                user.failed_login_attempts = 0
                details["locked_until"] = user.locked_until.isoformat()
            record_audit(
                self.session,
                action="auth.login_failed",
                meta=meta,
                organization_id=user.organization_id,
                actor_id=user.id,
                details=details,
            )
            await self.session.commit()
            raise AuthenticationError(_INVALID_CREDENTIALS, code="invalid_credentials")

        if not user.is_active:
            raise PermissionDeniedError("This account has been deactivated", code="account_disabled")

        user.failed_login_attempts = 0
        user.locked_until = None
        user.last_login_at = now
        issued = await self._issue_tokens(user, meta, family_id=None)
        record_audit(
            self.session,
            action="auth.login",
            meta=meta,
            organization_id=user.organization_id,
            actor_id=user.id,
            resource_type="user",
            resource_id=user.id,
        )
        await self.session.commit()
        return issued

    # --- refresh ---------------------------------------------------------------------------
    async def refresh(self, raw_token: str | None, meta: RequestMeta) -> IssuedTokens:
        if not raw_token:
            raise AuthenticationError("Refresh token missing", code="refresh_token_missing")

        token = await self.tokens.get_by_hash(hash_token(raw_token), for_update=True)
        if token is None:
            raise AuthenticationError("Invalid refresh token", code="invalid_refresh_token")

        now = _now()
        if token.revoked_at is not None:
            within_grace = (now - token.revoked_at).total_seconds() <= self.settings.refresh_reuse_grace_seconds
            if token.replaced_by_id is not None and within_grace:
                # Concurrent refresh from the same client: reject this one but don't punish the session.
                raise AuthenticationError("Refresh token already rotated", code="refresh_token_rotated")
            await self.tokens.revoke_family(token.family_id, now)
            record_audit(
                self.session,
                action="auth.refresh_reuse_detected",
                meta=meta,
                organization_id=None,
                actor_id=token.user_id,
                details={"family_id": str(token.family_id)},
            )
            await self.session.commit()
            log.warning("refresh_token_reuse_detected", user_id=str(token.user_id))
            raise AuthenticationError("Invalid refresh token", code="invalid_refresh_token")

        if token.expires_at <= now:
            raise AuthenticationError("Refresh token expired", code="refresh_token_expired")

        found = await self.users.get_with_organization(token.user_id)
        if found is None or not found[0].is_active:
            await self.tokens.revoke_family(token.family_id, now)
            await self.session.commit()
            raise AuthenticationError("Invalid refresh token", code="invalid_refresh_token")
        user = found[0]

        issued, new_record = await self._issue_tokens_with_record(user, meta, family_id=token.family_id)
        await self.session.flush()  # insert the successor before referencing it
        token.revoked_at = now
        token.replaced_by_id = new_record.id
        await self.session.commit()
        return issued

    # --- logout / password -----------------------------------------------------------------
    async def logout(self, raw_token: str | None, meta: RequestMeta) -> None:
        if not raw_token:
            return
        token = await self.tokens.get_by_hash(hash_token(raw_token))
        if token is None:
            return
        await self.tokens.revoke_family(token.family_id, _now())
        record_audit(
            self.session,
            action="auth.logout",
            meta=meta,
            organization_id=None,
            actor_id=token.user_id,
        )
        await self.session.commit()

    async def change_password(
        self, user_id: uuid.UUID, organization_id: uuid.UUID, data: ChangePasswordRequest, meta: RequestMeta
    ) -> None:
        user = await self.users.get_in_org(organization_id, user_id)
        if user is None:
            raise AuthenticationError("Not authenticated")
        if not verify_password(data.current_password, user.password_hash):
            raise ValidationFailedError("Current password is incorrect", code="invalid_current_password")
        ensure_password_strength(data.new_password, self.settings)
        if verify_password(data.new_password, user.password_hash):
            raise ValidationFailedError("New password must differ from the current one", code="password_reused")

        user.password_hash = hash_password(data.new_password)
        await self.tokens.revoke_all_for_user(user.id, _now())  # sign out every session
        record_audit(
            self.session,
            action="auth.password_changed",
            meta=meta,
            organization_id=organization_id,
            actor_id=user.id,
            resource_type="user",
            resource_id=user.id,
        )
        await self.session.commit()

    # --- helpers ---------------------------------------------------------------------------
    async def _issue_tokens(self, user: User, meta: RequestMeta, family_id: uuid.UUID | None) -> IssuedTokens:
        issued, _ = await self._issue_tokens_with_record(user, meta, family_id)
        return issued

    async def _issue_tokens_with_record(
        self, user: User, meta: RequestMeta, family_id: uuid.UUID | None
    ) -> tuple[IssuedTokens, RefreshToken]:
        access, expires_in = create_access_token(
            user_id=user.id, organization_id=user.organization_id, role=user.role.value, settings=self.settings
        )
        raw_refresh = generate_refresh_token()
        expires_at = _now() + timedelta(days=self.settings.refresh_token_ttl_days)
        record = RefreshToken(
            id=uuid7(),
            user_id=user.id,
            token_hash=hash_token(raw_refresh),
            family_id=family_id or uuid7(),
            expires_at=expires_at,
            user_agent=(meta.user_agent or "")[:512] or None,
            ip=meta.ip,
        )
        self.tokens.add(record)
        return IssuedTokens(access, expires_in, raw_refresh, expires_at, user), record
