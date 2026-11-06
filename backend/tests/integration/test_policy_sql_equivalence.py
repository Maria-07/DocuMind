"""The SQL visibility predicate (listings) must agree with the Python policy (single-document access)."""

from __future__ import annotations

import itertools
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.permissions import Principal, permissions_for
from app.db.session import create_sessionmaker
from app.models import Document, DocumentPermission, GroupMember, Organization, User, UserGroup
from app.models.enums import DocumentVisibility, FileType, GrantPermission, PrincipalType, Role
from app.repositories.documents import visible_documents_clause
from app.services.access_policy import can_view


async def test_sql_clause_matches_can_view(engine: AsyncEngine) -> None:
    sessionmaker = create_sessionmaker(engine)
    async with sessionmaker() as s:
        org, other_org = Organization(name="A", slug="a"), Organization(name="B", slug="b")
        s.add_all([org, other_org])
        await s.flush()

        def user(name: str, role: Role, o: Organization = org) -> User:
            return User(
                organization_id=o.id,
                email=f"{name}@x.com",
                password_hash="x",
                full_name=name,
                role=role,
                is_active=True,
                failed_login_attempts=0,
            )

        owner, alice, admin, manager, outsider = (
            user("owner", Role.USER),
            user("alice", Role.USER),
            user("admin", Role.ADMIN),
            user("manager", Role.MANAGER),
            user("outsider", Role.ADMIN, other_org),
        )
        s.add_all([owner, alice, admin, manager, outsider])
        await s.flush()
        group = UserGroup(organization_id=org.id, name="g")
        s.add(group)
        await s.flush()
        s.add(GroupMember(group_id=group.id, user_id=alice.id))

        documents: list[tuple[Document, list[DocumentPermission]]] = []
        grant_options = [
            [],
            [(PrincipalType.USER, alice.id)],
            [(PrincipalType.GROUP, group.id)],
            [(PrincipalType.USER, manager.id)],
        ]
        for i, (vis, grant_spec) in enumerate(itertools.product(DocumentVisibility, grant_options)):
            d = Document(
                organization_id=org.id,
                owner_id=owner.id,
                title=f"d{i}",
                original_filename=f"d{i}.pdf",
                file_type=FileType.PDF,
                mime_type="application/pdf",
                size_bytes=1,
                sha256=uuid.uuid4().hex,
                storage_key=f"k/{i}",
                visibility=vis,
                active_index_version=0,
                chunk_count=0,
                doc_metadata={},
            )
            s.add(d)
            await s.flush()
            grants = [
                DocumentPermission(
                    document_id=d.id, principal_type=pt, principal_id=pid, permission=GrantPermission.VIEW
                )
                for pt, pid in grant_spec
            ]
            s.add_all(grants)
            documents.append((d, grants))
        await s.commit()

        groups_of = {alice.id: frozenset({group.id})}
        for u in (owner, alice, admin, manager, outsider):
            p = Principal(
                user_id=u.id,
                organization_id=u.organization_id,
                role=u.role,
                email=u.email,
                group_ids=groups_of.get(u.id, frozenset()),
                permissions=permissions_for(u.role),
            )
            sql_visible = set((await s.execute(select(Document.id).where(visible_documents_clause(p)))).scalars())
            py_visible = {d.id for d, grants in documents if can_view(p, d, grants)}
            assert sql_visible == py_visible, u.full_name
