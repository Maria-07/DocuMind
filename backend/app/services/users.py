"""User administration within an organization (Admin only — enforced at the API layer)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.core.permissions import Principal
from app.core.security import hash_password
from app.models import Organization, User
from app.models.enums import Role
from app.repositories.users import OrganizationRepository, RefreshTokenRepository, UserRepository
from app.schemas.common import PageParams
from app.schemas.organization import OrganizationSettings, OrganizationUpdate
from app.schemas.user import UserCreate, UserUpdate
from app.services.audit import RequestMeta, record_audit
from app.services.auth import ensure_password_strength


class UserService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.users = UserRepository(session)
        self.tokens = RefreshTokenRepository(session)

    async def list(
        self,
        principal: Principal,
        page: PageParams,
        *,
        q: str | None = None,
        role: Role | None = None,
        is_active: bool | None = None,
    ) -> tuple[Sequence[User], int]:
        return await self.users.list_in_org(
            principal.organization_id,
            q=q,
            role=role,
            is_active=is_active,
            offset=page.offset,
            limit=page.page_size,
        )

    async def get(self, principal: Principal, user_id: uuid.UUID) -> User:
        user = await self.users.get_in_org(principal.organization_id, user_id)
        if user is None:
            raise NotFoundError("User not found", code="user_not_found")
        return user

    async def create(self, principal: Principal, data: UserCreate, meta: RequestMeta) -> User:
        ensure_password_strength(data.password, self.settings)
        email = data.email.lower()
        if await self.users.get_by_email(email) is not None:
            raise ConflictError("An account with this e-mail already exists", code="email_taken")
        user = User(
            organization_id=principal.organization_id,
            email=email,
            password_hash=hash_password(data.password),
            full_name=data.full_name.strip(),
            role=data.role,
            is_active=True,
            failed_login_attempts=0,
        )
        self.users.add(user)
        await self.session.flush()
        record_audit(
            self.session,
            action="user.created",
            meta=meta,
            organization_id=principal.organization_id,
            actor_id=principal.user_id,
            resource_type="user",
            resource_id=user.id,
            details={"role": data.role.value},
        )
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError("An account with this e-mail already exists", code="email_taken") from exc
        return user

    async def update(self, principal: Principal, user_id: uuid.UUID, data: UserUpdate, meta: RequestMeta) -> User:
        user = await self.get(principal, user_id)
        changes = data.model_dump(exclude_unset=True)

        losing_admin = user.role == Role.ADMIN and (
            ("role" in changes and changes["role"] != Role.ADMIN) or changes.get("is_active") is False
        )
        if losing_admin and user.is_active and await self.users.count_active_admins(principal.organization_id) <= 1:
            raise ValidationFailedError("The organization must keep at least one active admin", code="last_admin")

        if "full_name" in changes and changes["full_name"] is not None:
            user.full_name = changes["full_name"].strip()
        if "role" in changes and changes["role"] is not None and changes["role"] != user.role:
            record_audit(
                self.session,
                action="user.role_changed",
                meta=meta,
                organization_id=principal.organization_id,
                actor_id=principal.user_id,
                resource_type="user",
                resource_id=user.id,
                details={"from": user.role.value, "to": changes["role"].value},
            )
            user.role = changes["role"]
        if "is_active" in changes and changes["is_active"] is not None and changes["is_active"] != user.is_active:
            user.is_active = changes["is_active"]
            if not user.is_active:
                await self.tokens.revoke_all_for_user(user.id, datetime.now(UTC))
            record_audit(
                self.session,
                action="user.deactivated" if not user.is_active else "user.reactivated",
                meta=meta,
                organization_id=principal.organization_id,
                actor_id=principal.user_id,
                resource_type="user",
                resource_id=user.id,
            )
        await self.session.commit()
        return user


class OrganizationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.orgs = OrganizationRepository(session)

    async def get(self, principal: Principal) -> Organization:
        org = await self.orgs.get(principal.organization_id)
        if org is None:
            raise NotFoundError("Organization not found")
        return org

    async def update(self, principal: Principal, data: OrganizationUpdate, meta: RequestMeta) -> Organization:
        org = await self.get(principal)
        changes = data.model_dump(exclude_unset=True)
        if changes.get("name"):
            org.name = changes["name"].strip()
        if data.settings is not None:
            merged = OrganizationSettings.model_validate(
                {**org.settings, **data.settings.model_dump(exclude_unset=True)}
            )
            org.settings = merged.model_dump(mode="json")
        record_audit(
            self.session,
            action="organization.updated",
            meta=meta,
            organization_id=org.id,
            actor_id=principal.user_id,
            resource_type="organization",
            resource_id=org.id,
            details={"fields": sorted(changes)},
        )
        await self.session.commit()
        return org
