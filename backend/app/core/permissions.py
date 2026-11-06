"""Role → permission mapping and the authenticated Principal.

Endpoints check *permissions*, never role names, so the role model can evolve in one place.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum

from app.models.enums import Role


class Permission(StrEnum):
    USERS_MANAGE = "users:manage"
    GROUPS_MANAGE = "groups:manage"
    ORGANIZATION_MANAGE = "organization:manage"
    DOCUMENTS_UPLOAD = "documents:upload"
    # View and manage every document in the organization, regardless of visibility/grants.
    DOCUMENTS_ADMIN = "documents:admin"
    CATEGORIES_MANAGE = "categories:manage"
    EVALUATIONS_RUN = "evaluations:run"
    EVALUATIONS_VIEW = "evaluations:view"
    ANALYTICS_ORGANIZATION = "analytics:organization"
    AUDIT_VIEW = "audit:view"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset(Permission),
    Role.MANAGER: frozenset({Permission.DOCUMENTS_UPLOAD, Permission.CATEGORIES_MANAGE, Permission.EVALUATIONS_VIEW}),
    Role.USER: frozenset(),
}


def permissions_for(role: Role, *, users_can_upload: bool = False) -> frozenset[Permission]:
    perms = ROLE_PERMISSIONS[role]
    if role is Role.USER and users_can_upload:
        perms = perms | {Permission.DOCUMENTS_UPLOAD}
    return perms


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller. Built server-side from the DB on every request — never from client input."""

    user_id: uuid.UUID
    organization_id: uuid.UUID
    role: Role
    email: str
    group_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)
    permissions: frozenset[Permission] = field(default_factory=frozenset)

    def has(self, permission: Permission) -> bool:
        return permission in self.permissions
