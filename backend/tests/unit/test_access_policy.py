"""The access policy is security-critical: exhaustively check it, and check that the vector-store ACL
token representation is exactly equivalent to ``can_view``."""

from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass

import pytest

from app.core.permissions import Principal, permissions_for
from app.models.enums import DocumentVisibility, GrantPermission, PrincipalType, Role
from app.services.access_policy import can_manage, can_view, document_acl_tokens, principal_acl_tokens

ORG = uuid.uuid4()
OTHER_ORG = uuid.uuid4()
OWNER = uuid.uuid4()
ALICE = uuid.uuid4()
GROUP = uuid.uuid4()


@dataclass
class Doc:
    organization_id: uuid.UUID
    owner_id: uuid.UUID | None
    visibility: DocumentVisibility


@dataclass
class Grant:
    principal_type: PrincipalType
    principal_id: uuid.UUID
    permission: GrantPermission


def principal(
    user_id: uuid.UUID = ALICE,
    role: Role = Role.USER,
    org: uuid.UUID = ORG,
    groups: frozenset[uuid.UUID] = frozenset(),
) -> Principal:
    return Principal(
        user_id=user_id,
        organization_id=org,
        role=role,
        email="x@example.com",
        group_ids=groups,
        permissions=permissions_for(role),
    )


def doc(visibility: DocumentVisibility, org: uuid.UUID = ORG, owner: uuid.UUID | None = OWNER) -> Doc:
    return Doc(organization_id=org, owner_id=owner, visibility=visibility)


V = DocumentVisibility


def test_other_org_never_visible_even_to_admin() -> None:
    admin = principal(role=Role.ADMIN)
    for vis in V:
        d = doc(vis, org=OTHER_ORG)
        assert not can_view(admin, d, [])
        assert not can_manage(admin, d, [])


@pytest.mark.parametrize("vis", list(V))
def test_owner_and_admin_always_view_and_manage(vis: DocumentVisibility) -> None:
    for p in (principal(user_id=OWNER), principal(role=Role.ADMIN)):
        assert can_view(p, doc(vis), [])
        assert can_manage(p, doc(vis), [])


def test_organization_visibility() -> None:
    assert can_view(principal(), doc(V.ORGANIZATION), [])
    assert not can_manage(principal(), doc(V.ORGANIZATION), [])
    # Managers are not document admins: an org-visible document is still read-only for them.
    assert not can_manage(principal(role=Role.MANAGER), doc(V.ORGANIZATION), [])


def test_private_ignores_grants() -> None:
    grants = [Grant(PrincipalType.USER, ALICE, GrantPermission.MANAGE)]
    assert not can_view(principal(), doc(V.PRIVATE), grants)
    assert not can_manage(principal(), doc(V.PRIVATE), grants)


def test_restricted_requires_grant() -> None:
    assert not can_view(principal(), doc(V.RESTRICTED), [])
    user_grant = [Grant(PrincipalType.USER, ALICE, GrantPermission.VIEW)]
    assert can_view(principal(), doc(V.RESTRICTED), user_grant)
    assert not can_manage(principal(), doc(V.RESTRICTED), user_grant)


def test_group_grants() -> None:
    grants = [Grant(PrincipalType.GROUP, GROUP, GrantPermission.MANAGE)]
    member = principal(groups=frozenset({GROUP}))
    assert can_view(member, doc(V.RESTRICTED), grants)
    assert can_manage(member, doc(V.RESTRICTED), grants)
    assert not can_view(principal(), doc(V.RESTRICTED), grants)


def test_grant_to_someone_else_does_not_apply() -> None:
    grants = [Grant(PrincipalType.USER, uuid.uuid4(), GrantPermission.MANAGE)]
    assert not can_view(principal(), doc(V.RESTRICTED), grants)


def test_group_id_equal_to_user_id_does_not_confuse_types() -> None:
    # A USER grant whose id happens to be one of the caller's group ids must not match.
    grants = [Grant(PrincipalType.USER, GROUP, GrantPermission.VIEW)]
    assert not can_view(principal(groups=frozenset({GROUP})), doc(V.RESTRICTED), grants)


def test_manage_grant_applies_to_org_visible_documents() -> None:
    grants = [Grant(PrincipalType.USER, ALICE, GrantPermission.MANAGE)]
    assert can_manage(principal(), doc(V.ORGANIZATION), grants)


def test_ownerless_document_after_owner_removed() -> None:
    d = doc(V.RESTRICTED, owner=None)
    assert not can_view(principal(), d, [])
    assert can_view(principal(role=Role.ADMIN), d, [])


# --- Equivalence: ACL tokens ⟺ can_view ---------------------------------------------------

_PRINCIPALS = [
    principal(),
    principal(user_id=OWNER),
    principal(role=Role.ADMIN),
    principal(role=Role.MANAGER),
    principal(groups=frozenset({GROUP})),
    principal(org=OTHER_ORG),
    principal(org=OTHER_ORG, role=Role.ADMIN),
]
_GRANT_SETS = [
    [],
    [Grant(PrincipalType.USER, ALICE, GrantPermission.VIEW)],
    [Grant(PrincipalType.GROUP, GROUP, GrantPermission.VIEW)],
    [Grant(PrincipalType.USER, uuid.uuid4(), GrantPermission.MANAGE)],
]
_DOCS = [doc(v, owner=o) for v, o in itertools.product(V, [OWNER, None])]


@pytest.mark.parametrize(("p", "d", "grants"), list(itertools.product(_PRINCIPALS, _DOCS, _GRANT_SETS)))
def test_acl_tokens_match_can_view(p: Principal, d: Doc, grants: list[Grant]) -> None:
    token_match = bool(set(principal_acl_tokens(p)) & set(document_acl_tokens(d, grants)))
    assert token_match == can_view(p, d, grants)
