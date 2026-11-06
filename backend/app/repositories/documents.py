from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence

from sqlalchemy import ColumnElement, and_, delete, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import Permission, Principal
from app.models import Document, DocumentChunk, DocumentPermission, IngestionJob
from app.models.enums import DocumentVisibility, PrincipalType
from app.schemas.document import DocumentListFilters
from app.utils.text import escape_like


def visible_documents_clause(principal: Principal) -> ColumnElement[bool]:
    """SQL twin of services.access_policy.can_view — keep the two in sync (both are tested)."""
    same_org = Document.organization_id == principal.organization_id
    not_deleted = Document.deleted_at.is_(None)
    if principal.has(Permission.DOCUMENTS_ADMIN):
        return and_(same_org, not_deleted)

    principal_match = or_(
        and_(
            DocumentPermission.principal_type == PrincipalType.USER,
            DocumentPermission.principal_id == principal.user_id,
        ),
        and_(
            DocumentPermission.principal_type == PrincipalType.GROUP,
            DocumentPermission.principal_id.in_(list(principal.group_ids)),
        ),
    )
    has_grant = exists().where(DocumentPermission.document_id == Document.id, principal_match)
    return and_(
        same_org,
        not_deleted,
        or_(
            Document.owner_id == principal.user_id,
            Document.visibility == DocumentVisibility.ORGANIZATION,
            and_(Document.visibility == DocumentVisibility.RESTRICTED, has_grant),
        ),
    )


_SORTS = {
    "created_at": Document.created_at.asc(),
    "-created_at": Document.created_at.desc(),
    # Case-insensitive and independent of the database collation.
    "title": func.lower(Document.title).asc(),
    "-title": func.lower(Document.title).desc(),
    "size_bytes": Document.size_bytes.asc(),
    "-size_bytes": Document.size_bytes.desc(),
}


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_in_org(
        self, organization_id: uuid.UUID, document_id: uuid.UUID, *, include_deleted: bool = False
    ) -> Document | None:
        stmt = select(Document).where(Document.id == document_id, Document.organization_id == organization_id)
        if not include_deleted:
            stmt = stmt.where(Document.deleted_at.is_(None))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_any(self, document_id: uuid.UUID) -> Document | None:
        """Unscoped lookup — for background workers only (they act on ids they enqueued)."""
        return await self.session.get(Document, document_id)

    async def find_active_by_hash(self, organization_id: uuid.UUID, sha256: str) -> Document | None:
        stmt = select(Document).where(
            Document.organization_id == organization_id,
            Document.sha256 == sha256,
            Document.deleted_at.is_(None),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_visible(
        self, principal: Principal, filters: DocumentListFilters, *, offset: int, limit: int
    ) -> tuple[Sequence[Document], int]:
        conditions: list[ColumnElement[bool]] = [visible_documents_clause(principal)]
        if filters.q:
            pattern = f"%{escape_like(filters.q)}%"
            conditions.append(or_(Document.title.ilike(pattern), Document.original_filename.ilike(pattern)))
        if filters.status is not None:
            conditions.append(Document.status == filters.status)
        if filters.category_id is not None:
            conditions.append(Document.category_id == filters.category_id)
        if filters.file_type is not None:
            conditions.append(Document.file_type == filters.file_type)
        if filters.owner_id is not None:
            conditions.append(Document.owner_id == filters.owner_id)
        if filters.created_from is not None:
            conditions.append(Document.created_at >= filters.created_from)
        if filters.created_to is not None:
            conditions.append(Document.created_at <= filters.created_to)

        total = await self.session.scalar(select(func.count()).select_from(Document).where(*conditions))
        stmt = (
            select(Document).where(*conditions).order_by(_SORTS[filters.sort], Document.id).offset(offset).limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all(), int(total or 0)

    async def storage_used_bytes(self, organization_id: uuid.UUID) -> int:
        stmt = select(func.coalesce(func.sum(Document.size_bytes), 0)).where(
            Document.organization_id == organization_id, Document.purged_at.is_(None)
        )
        return int(await self.session.scalar(stmt) or 0)

    # --- grants ----------------------------------------------------------------------------
    async def grants_for(self, document_id: uuid.UUID) -> list[DocumentPermission]:
        stmt = (
            select(DocumentPermission)
            .where(DocumentPermission.document_id == document_id)
            .order_by(DocumentPermission.created_at)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def grants_for_many(self, document_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, list[DocumentPermission]]:
        grants: dict[uuid.UUID, list[DocumentPermission]] = defaultdict(list)
        if not document_ids:
            return grants
        stmt = select(DocumentPermission).where(DocumentPermission.document_id.in_(document_ids))
        for grant in (await self.session.execute(stmt)).scalars().all():
            grants[grant.document_id].append(grant)
        return grants

    async def replace_grants(self, document_id: uuid.UUID, grants: Sequence[DocumentPermission]) -> None:
        await self.session.execute(delete(DocumentPermission).where(DocumentPermission.document_id == document_id))
        await self.session.flush()
        self.session.add_all(grants)

    # --- chunks ----------------------------------------------------------------------------
    async def list_chunks(
        self, document_id: uuid.UUID, index_version: int, *, offset: int, limit: int
    ) -> tuple[Sequence[DocumentChunk], int]:
        conditions = (DocumentChunk.document_id == document_id, DocumentChunk.index_version == index_version)
        total = await self.session.scalar(select(func.count()).select_from(DocumentChunk).where(*conditions))
        stmt = select(DocumentChunk).where(*conditions).order_by(DocumentChunk.chunk_index).offset(offset).limit(limit)
        return (await self.session.execute(stmt)).scalars().all(), int(total or 0)

    # --- jobs ------------------------------------------------------------------------------
    async def latest_job(self, document_id: uuid.UUID) -> IngestionJob | None:
        stmt = (
            select(IngestionJob)
            .where(IngestionJob.document_id == document_id)
            .order_by(IngestionJob.created_at.desc(), IngestionJob.id.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_jobs(self, document_id: uuid.UUID, limit: int = 50) -> Sequence[IngestionJob]:
        stmt = (
            select(IngestionJob)
            .where(IngestionJob.document_id == document_id)
            .order_by(IngestionJob.created_at.desc(), IngestionJob.id.desc())
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    def add(self, obj: Document | IngestionJob) -> None:
        self.session.add(obj)
