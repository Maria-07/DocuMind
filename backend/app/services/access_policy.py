"""Document access rules — the single source of truth for who may view or manage a document.

Three representations must agree and are tested against each other:
  * ``can_view`` / ``can_manage`` (this module, Python),
  * ``repositories.documents.visible_documents_clause`` (SQL, for listings),
  * ``document_acl_tokens`` ∩ ``principal_acl_tokens`` (Qdrant payload filter, used in retrieval).

Rules
-----
* Never across organizations.
* ``documents:admin`` (Admin role) → view + manage everything in the org.
* Owner → view + manage.
* ``organization`` visibility → everyone in the org may view.
* ``restricted`` visibility → users/groups with a grant may view; ``manage`` grants may manage.
* ``private`` → owner and admins only; grants are ignored.
* ``manage`` grants also apply to ``organization`` documents.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Protocol

from app.core.permissions import Permission, Principal
from app.models.enums import DocumentVisibility, GrantPermission, PrincipalType


class DocumentLike(Protocol):
    organization_id: uuid.UUID
    owner_id: uuid.UUID | None
    visibility: DocumentVisibility


class GrantLike(Protocol):
    principal_type: PrincipalType
    principal_id: uuid.UUID
    permission: GrantPermission


def _grant_matches(principal: Principal, grant: GrantLike) -> bool:
    if grant.principal_type == PrincipalType.USER:
        return grant.principal_id == principal.user_id
    return grant.principal_id in principal.group_ids


def can_view(principal: Principal, document: DocumentLike, grants: Iterable[GrantLike]) -> bool:
    if document.organization_id != principal.organization_id:
        return False
    if principal.has(Permission.DOCUMENTS_ADMIN) or document.owner_id == principal.user_id:
        return True
    if document.visibility == DocumentVisibility.ORGANIZATION:
        return True
    if document.visibility == DocumentVisibility.RESTRICTED:
        return any(_grant_matches(principal, g) for g in grants)
    return False


def can_manage(principal: Principal, document: DocumentLike, grants: Iterable[GrantLike]) -> bool:
    if document.organization_id != principal.organization_id:
        return False
    if principal.has(Permission.DOCUMENTS_ADMIN) or document.owner_id == principal.user_id:
        return True
    if document.visibility == DocumentVisibility.PRIVATE:
        return False
    return any(g.permission == GrantPermission.MANAGE and _grant_matches(principal, g) for g in grants)


# --- Vector-store ACL tokens ---------------------------------------------------------------
# A point is retrievable by a principal iff the two token sets intersect. Every token is
# organization-qualified, so the tokens alone can never match across tenants (the retrieval
# filter additionally pins org_id — defense in depth).


def document_acl_tokens(document: DocumentLike, grants: Iterable[GrantLike]) -> list[str]:
    org = document.organization_id
    tokens = [f"admin:{org}"]
    if document.owner_id is not None:
        tokens.append(f"user:{org}:{document.owner_id}")
    if document.visibility == DocumentVisibility.ORGANIZATION:
        tokens.append(f"org:{org}")
    if document.visibility == DocumentVisibility.RESTRICTED:
        for g in grants:
            tokens.append(f"{g.principal_type.value}:{org}:{g.principal_id}")
    return sorted(set(tokens))


def principal_acl_tokens(principal: Principal) -> list[str]:
    org = principal.organization_id
    tokens = [f"user:{org}:{principal.user_id}", f"org:{org}"]
    tokens.extend(f"group:{org}:{gid}" for gid in principal.group_ids)
    if principal.has(Permission.DOCUMENTS_ADMIN):
        tokens.append(f"admin:{org}")
    return sorted(tokens)
