from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GroupMember, Organization, RefreshToken, User
from app.models.enums import Role
from app.utils.text import escape_like


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_email(self, email: str) -> User | None:
        result = await self.session.execute(select(User).where(User.email == email.lower()))
        return result.scalar_one_or_none()

    async def get_with_organization(self, user_id: uuid.UUID) -> tuple[User, Organization] | None:
        stmt = (
            select(User, Organization)
            .join(Organization, Organization.id == User.organization_id)
            .where(User.id == user_id)
        )
        row = (await self.session.execute(stmt)).one_or_none()
        return (row[0], row[1]) if row else None

    async def get_in_org(self, organization_id: uuid.UUID, user_id: uuid.UUID) -> User | None:
        stmt = select(User).where(User.id == user_id, User.organization_id == organization_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_in_org(
        self,
        organization_id: uuid.UUID,
        *,
        q: str | None,
        role: Role | None,
        is_active: bool | None,
        offset: int,
        limit: int,
    ) -> tuple[Sequence[User], int]:
        conditions = [User.organization_id == organization_id]
        if q:
            pattern = f"%{escape_like(q)}%"
            conditions.append(or_(User.email.ilike(pattern), User.full_name.ilike(pattern)))
        if role is not None:
            conditions.append(User.role == role)
        if is_active is not None:
            conditions.append(User.is_active == is_active)

        total = await self.session.scalar(select(func.count()).select_from(User).where(*conditions))
        stmt = select(User).where(*conditions).order_by(User.created_at.desc()).offset(offset).limit(limit)
        return (await self.session.execute(stmt)).scalars().all(), int(total or 0)

    async def count_active_admins(self, organization_id: uuid.UUID) -> int:
        stmt = select(func.count()).where(
            User.organization_id == organization_id, User.role == Role.ADMIN, User.is_active.is_(True)
        )
        return int(await self.session.scalar(stmt) or 0)

    async def ids_existing_in_org(self, organization_id: uuid.UUID, user_ids: Sequence[uuid.UUID]) -> set[uuid.UUID]:
        if not user_ids:
            return set()
        stmt = select(User.id).where(User.organization_id == organization_id, User.id.in_(user_ids))
        return set((await self.session.execute(stmt)).scalars().all())

    async def group_ids_for(self, user_id: uuid.UUID) -> frozenset[uuid.UUID]:
        stmt = select(GroupMember.group_id).where(GroupMember.user_id == user_id)
        return frozenset((await self.session.execute(stmt)).scalars().all())

    def add(self, user: User) -> None:
        self.session.add(user)


class RefreshTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_hash(self, token_hash: str, *, for_update: bool = False) -> RefreshToken | None:
        stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        if for_update:
            stmt = stmt.with_for_update()
        return (await self.session.execute(stmt)).scalar_one_or_none()

    def add(self, token: RefreshToken) -> None:
        self.session.add(token)

    async def revoke_family(self, family_id: uuid.UUID, now: datetime) -> None:
        await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )

    async def revoke_all_for_user(self, user_id: uuid.UUID, now: datetime) -> None:
        await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )


class OrganizationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, organization_id: uuid.UUID) -> Organization | None:
        return await self.session.get(Organization, organization_id)

    async def slug_exists(self, slug: str) -> bool:
        stmt = select(func.count()).where(Organization.slug == slug)
        return bool(await self.session.scalar(stmt))

    def add(self, organization: Organization) -> None:
        self.session.add(organization)
