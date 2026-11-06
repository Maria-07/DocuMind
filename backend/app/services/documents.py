"""Document management use cases. Authorization is enforced here *and* in the repository SQL."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import aiofiles
import anyio
import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError, ValidationFailedError
from app.core.permissions import Permission, Principal
from app.models import Document, DocumentChunk, DocumentPermission, IngestionJob
from app.models.enums import (
    DocumentStatus,
    DocumentVisibility,
    FileType,
    JobStatus,
    PrincipalType,
)
from app.repositories.categories import CategoryRepository
from app.repositories.documents import DocumentRepository
from app.repositories.groups import GroupRepository
from app.repositories.users import OrganizationRepository, UserRepository
from app.schemas.common import ErrorInfo, PageParams
from app.schemas.document import (
    DocumentDetail,
    DocumentListFilters,
    DocumentRead,
    DocumentUpdate,
    GrantInput,
    IngestionJobRead,
    UploadResponse,
    UploadResult,
)
from app.schemas.organization import OrganizationSettings
from app.services.access_policy import can_manage, can_view
from app.services.audit import RequestMeta, record_audit
from app.services.file_validation import FileValidationError, detect_file_type, validate_content
from app.services.job_queue import INGEST_DOCUMENT, PURGE_DOCUMENT, SYNC_DOCUMENT_PAYLOAD, JobQueue
from app.services.storage import LocalStorage

if TYPE_CHECKING:
    from app.rag.vectorstore import QdrantVectorStore
from app.utils.ids import uuid7
from app.utils.text import sanitize_filename

log = structlog.get_logger(__name__)

_SPOOL_CHUNK = 1024 * 1024


class UploadSource(Protocol):
    """What we need from an uploaded file (satisfied by fastapi.UploadFile)."""

    filename: str | None

    async def read(self, size: int = -1) -> bytes: ...


@dataclass(slots=True)
class _QuotaState:
    quota: int | None
    used: int


@dataclass(frozen=True, slots=True)
class DownloadHandle:
    document: Document
    stream: AsyncIterator[bytes]


def _now() -> datetime:
    return datetime.now(UTC)


class DocumentService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        storage: LocalStorage,
        queue: JobQueue,
        vector_store: QdrantVectorStore | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.storage = storage
        self.queue = queue
        self.vector_store = vector_store
        self.docs = DocumentRepository(session)
        self.categories = CategoryRepository(session)
        self.orgs = OrganizationRepository(session)
        self.users = UserRepository(session)
        self.groups = GroupRepository(session)

    # =======================================================================================
    # Upload
    # =======================================================================================
    async def upload_many(
        self,
        principal: Principal,
        files: Sequence[UploadSource],
        *,
        category_id: uuid.UUID | None,
        visibility: DocumentVisibility | None,
        meta: RequestMeta,
    ) -> UploadResponse:
        if not principal.has(Permission.DOCUMENTS_UPLOAD):
            raise PermissionDeniedError("You are not allowed to upload documents")
        if not files:
            raise ValidationFailedError("No files were provided", code="no_files")
        if len(files) > self.settings.max_files_per_upload:
            raise ValidationFailedError(
                f"At most {self.settings.max_files_per_upload} files per upload", code="too_many_files"
            )

        org = await self.orgs.get(principal.organization_id)
        if org is None:
            raise NotFoundError("Organization not found")
        org_settings = OrganizationSettings.model_validate(org.settings)
        effective_visibility = (
            visibility
            or org_settings.default_document_visibility
            or DocumentVisibility(self.settings.default_document_visibility)
        )
        if category_id is not None and await self.categories.get_in_org(org.id, category_id) is None:
            raise ValidationFailedError("Unknown category", code="unknown_category")

        quota = _QuotaState(
            quota=org.storage_quota_bytes,
            used=await self.docs.storage_used_bytes(org.id) if org.storage_quota_bytes else 0,
        )

        results: list[UploadResult] = []
        for source in files:
            results.append(await self._upload_one(principal, source, effective_visibility, category_id, quota, meta))
        return UploadResponse(
            results=results,
            accepted=sum(r.status == "accepted" for r in results),
            duplicates=sum(r.status == "duplicate" for r in results),
            rejected=sum(r.status == "rejected" for r in results),
        )

    async def _upload_one(
        self,
        principal: Principal,
        source: UploadSource,
        visibility: DocumentVisibility,
        category_id: uuid.UUID | None,
        quota: _QuotaState,
        meta: RequestMeta,
    ) -> UploadResult:
        filename = sanitize_filename(source.filename)
        org_id = principal.organization_id
        tmp_path = self.storage.tmp_dir / f"{uuid.uuid4().hex}.part"
        stored_key: str | None = None

        try:
            file_type = detect_file_type(filename)
            size, sha256 = await self._spool(source, tmp_path)
            validated = await anyio.to_thread.run_sync(
                lambda: validate_content(
                    tmp_path, file_type, max_uncompressed_bytes=self.settings.max_uncompressed_ooxml_bytes
                )
            )
            if quota.quota is not None and quota.used + size > quota.quota:
                raise FileValidationError("storage_quota_exceeded", "Organization storage quota exceeded")

            existing = await self.docs.find_active_by_hash(org_id, sha256)
            if existing is not None:
                return await self._duplicate_result(principal, filename, existing)

            document_id = uuid7()
            stored_key = f"{org_id}/{document_id}/original{validated.extension}"
            document = Document(
                id=document_id,
                organization_id=org_id,
                owner_id=principal.user_id,
                category_id=category_id,
                title=filename.rsplit(".", 1)[0][:300] or filename,
                original_filename=filename,
                file_type=validated.file_type,
                mime_type=validated.mime_type,
                size_bytes=size,
                sha256=sha256,
                storage_key=stored_key,
                status=DocumentStatus.UPLOADED,
                visibility=visibility,
                active_index_version=0,
                chunk_count=0,
                doc_metadata={},
            )
            job = IngestionJob(
                id=uuid7(),
                document_id=document_id,
                status=JobStatus.QUEUED,
                attempt=0,
                requested_by=principal.user_id,
                metrics={},
            )
            self.docs.add(document)
            self.docs.add(job)
            await self.session.flush()  # surfaces a concurrent duplicate as IntegrityError
            await self.storage.save_file(tmp_path, stored_key)
            record_audit(
                self.session,
                action="document.uploaded",
                meta=meta,
                organization_id=org_id,
                actor_id=principal.user_id,
                resource_type="document",
                resource_id=document_id,
                details={"file_type": file_type.value, "size_bytes": size},
            )
            await self.session.commit()
            quota.used += size
        except FileValidationError as exc:
            return UploadResult(
                filename=filename, status="rejected", error=ErrorInfo(code=exc.code, message=exc.message)
            )
        except IntegrityError:
            await self.session.rollback()
            await self._discard(stored_key)
            existing = await self.docs.find_active_by_hash(org_id, sha256)
            if existing is None:
                raise
            return await self._duplicate_result(principal, filename, existing)
        except BaseException:
            await self.session.rollback()
            await self._discard(stored_key)
            raise
        finally:
            tmp_path.unlink(missing_ok=True)

        await self._enqueue_ingestion(document, job)
        return UploadResult(
            filename=filename,
            status="accepted",
            document=DocumentRead.model_validate(document).model_copy(update={"can_manage": True}),
            job_id=job.id,
        )

    async def _spool(self, source: UploadSource, dest: Path) -> tuple[int, str]:
        """Copy the upload to a temp file in chunks, enforcing the size limit and hashing as we go."""
        limit = self.settings.max_upload_size_bytes
        hasher = hashlib.sha256()
        size = 0
        async with aiofiles.open(dest, "wb") as out:
            while chunk := await source.read(_SPOOL_CHUNK):
                size += len(chunk)
                if size > limit:
                    raise FileValidationError(
                        "file_too_large", f"File exceeds the {self.settings.max_upload_size_mb} MB limit"
                    )
                hasher.update(chunk)
                await out.write(chunk)
        if size == 0:
            raise FileValidationError("empty_file", "File is empty")
        return size, hasher.hexdigest()

    async def _duplicate_result(self, principal: Principal, filename: str, existing: Document) -> UploadResult:
        grants = await self.docs.grants_for(existing.id)
        visible = can_view(principal, existing, grants)
        return UploadResult(
            filename=filename,
            status="duplicate",
            # Only reveal which document it duplicates if the caller may see that document.
            document=(
                DocumentRead.model_validate(existing).model_copy(
                    update={"can_manage": can_manage(principal, existing, grants)}
                )
                if visible
                else None
            ),
            error=ErrorInfo(
                code="duplicate_document",
                message=(
                    f"Identical content already exists as '{existing.title}'"
                    if visible
                    else "Identical content already exists in this organization"
                ),
            ),
        )

    async def _discard(self, key: str | None) -> None:
        if key is None:
            return
        try:
            await self.storage.delete(key)
        except Exception:  # cleanup is best effort; the reconciliation job catches leftovers
            log.warning("storage_cleanup_failed", storage_key=key)

    async def _enqueue_ingestion(self, document: Document, job: IngestionJob) -> None:
        try:
            await self.queue.enqueue(INGEST_DOCUMENT, str(document.id), str(job.id), job_id=f"ingest:{job.id}")
        except Exception:
            # The job row stays 'queued'; the worker re-enqueues stale queued jobs on startup.
            log.exception("enqueue_failed", job="ingest", document_id=str(document.id), job_id=str(job.id))

    # =======================================================================================
    # Read
    # =======================================================================================
    async def list(
        self, principal: Principal, filters: DocumentListFilters, page: PageParams
    ) -> tuple[list[DocumentRead], int]:
        documents, total = await self.docs.list_visible(principal, filters, offset=page.offset, limit=page.page_size)
        grants = await self.docs.grants_for_many([d.id for d in documents])
        items = [
            DocumentRead.model_validate(d).model_copy(
                update={"can_manage": can_manage(principal, d, grants.get(d.id, []))}
            )
            for d in documents
        ]
        return items, total

    async def get_detail(self, principal: Principal, document_id: uuid.UUID) -> DocumentDetail:
        document, grants = await self._get_visible(principal, document_id)
        latest = await self.docs.latest_job(document.id)
        return DocumentDetail.model_validate(document).model_copy(
            update={
                "can_manage": can_manage(principal, document, grants),
                "latest_job": IngestionJobRead.model_validate(latest) if latest else None,
            }
        )

    async def list_jobs(self, principal: Principal, document_id: uuid.UUID) -> list[IngestionJobRead]:
        document, _ = await self._get_visible(principal, document_id)
        return [IngestionJobRead.model_validate(j) for j in await self.docs.list_jobs(document.id)]

    async def open_download(
        self, principal: Principal, document_id: uuid.UUID, meta: RequestMeta, *, inline: bool = False
    ) -> DownloadHandle:
        document, _ = await self._get_visible(principal, document_id)
        if inline and document.file_type is not FileType.PDF:
            raise ConflictError(
                "Inline preview is available for PDF files; use /documents/{id}/chunks for extracted content",
                code="preview_not_available",
            )
        if not await self.storage.exists(document.storage_key):
            log.error("stored_file_missing", document_id=str(document.id))
            raise NotFoundError("The original file is not available", code="file_missing")
        record_audit(
            self.session,
            action="document.previewed" if inline else "document.downloaded",
            meta=meta,
            organization_id=principal.organization_id,
            actor_id=principal.user_id,
            resource_type="document",
            resource_id=document.id,
        )
        await self.session.commit()
        return DownloadHandle(document=document, stream=self.storage.iter_bytes(document.storage_key))

    # =======================================================================================
    # Mutations
    # =======================================================================================
    async def update(
        self, principal: Principal, document_id: uuid.UUID, data: DocumentUpdate, meta: RequestMeta
    ) -> DocumentRead:
        document, grants = await self._get_manageable(principal, document_id)
        changes = data.model_dump(exclude_unset=True)

        if changes.get("title"):
            document.title = changes["title"].strip()
        if "category_id" in changes:
            new_category = changes["category_id"]
            if (
                new_category is not None
                and await self.categories.get_in_org(principal.organization_id, new_category) is None
            ):
                raise ValidationFailedError("Unknown category", code="unknown_category")
            document.category_id = new_category
        if changes.get("visibility") is not None and changes["visibility"] != document.visibility:
            record_audit(
                self.session,
                action="document.visibility_changed",
                meta=meta,
                organization_id=principal.organization_id,
                actor_id=principal.user_id,
                resource_type="document",
                resource_id=document.id,
                details={"from": document.visibility.value, "to": changes["visibility"].value},
            )
            document.visibility = changes["visibility"]

        await self.session.commit()
        if {"title", "category_id", "visibility"} & changes.keys():
            await self._enqueue_payload_sync(document)
        return DocumentRead.model_validate(document).model_copy(
            update={"can_manage": can_manage(principal, document, grants)}
        )

    async def delete(self, principal: Principal, document_id: uuid.UUID, meta: RequestMeta) -> None:
        document, _ = await self._get_manageable(principal, document_id)
        # Soft delete takes effect immediately: every listing/retrieval query excludes deleted_at.
        document.deleted_at = _now()
        record_audit(
            self.session,
            action="document.deleted",
            meta=meta,
            organization_id=principal.organization_id,
            actor_id=principal.user_id,
            resource_type="document",
            resource_id=document.id,
            details={"title": document.title},
        )
        await self.session.commit()
        await self._hide_vectors(document)
        try:
            await self.queue.enqueue(PURGE_DOCUMENT, str(document.id), job_id=f"purge:{document.id}")
        except Exception:
            log.exception("enqueue_failed", job="purge", document_id=str(document.id))

    async def _hide_vectors(self, document: Document) -> None:
        """Best effort: make the document's points unsearchable right away (purge deletes them later).

        Not required for correctness — retrieval re-checks every hit against PostgreSQL — but it keeps
        deleted content out of candidate lists immediately.
        """
        if self.vector_store is None or document.embedding_config_id is None:
            return
        try:
            with anyio.fail_after(3):
                await self.vector_store.set_document_payload(
                    self.vector_store.alias, document.id, {"searchable": False}
                )
        except Exception:
            log.warning("hide_vectors_failed", document_id=str(document.id))

    async def _enqueue_payload_sync(self, document: Document) -> None:
        if document.embedding_config_id is None:  # never indexed: nothing in Qdrant yet
            return
        try:
            await self.queue.enqueue(SYNC_DOCUMENT_PAYLOAD, str(document.id), job_id=f"sync:{document.id}:{uuid7()}")
        except Exception:
            # Retrieval re-checks permissions in PostgreSQL, so a missed sync can only narrow results.
            log.exception("enqueue_failed", job="sync", document_id=str(document.id))

    async def reprocess(self, principal: Principal, document_id: uuid.UUID, meta: RequestMeta) -> IngestionJobRead:
        document, _ = await self._get_manageable(principal, document_id)
        latest = await self.docs.latest_job(document.id)
        if document.status == DocumentStatus.PROCESSING or (
            latest is not None and latest.status in (JobStatus.QUEUED, JobStatus.RUNNING)
        ):
            raise ConflictError("This document is already queued or processing", code="reprocess_in_progress")

        job = IngestionJob(
            id=uuid7(),
            document_id=document.id,
            status=JobStatus.QUEUED,
            attempt=0,
            requested_by=principal.user_id,
            metrics={},
        )
        self.docs.add(job)
        record_audit(
            self.session,
            action="document.reprocess_requested",
            meta=meta,
            organization_id=principal.organization_id,
            actor_id=principal.user_id,
            resource_type="document",
            resource_id=document.id,
        )
        await self.session.commit()
        await self._enqueue_ingestion(document, job)
        return IngestionJobRead.model_validate(job)

    # --- permissions -----------------------------------------------------------------------
    async def get_permissions(
        self, principal: Principal, document_id: uuid.UUID
    ) -> tuple[Document, list[DocumentPermission]]:
        return await self._get_manageable(principal, document_id)

    async def replace_permissions(
        self, principal: Principal, document_id: uuid.UUID, grants_in: list[GrantInput], meta: RequestMeta
    ) -> tuple[Document, list[DocumentPermission]]:
        document, _ = await self._get_manageable(principal, document_id)

        deduped: dict[tuple[PrincipalType, uuid.UUID], GrantInput] = {}
        for grant in grants_in:
            deduped[(grant.principal_type, grant.principal_id)] = grant

        user_ids = [pid for (ptype, pid) in deduped if ptype == PrincipalType.USER]
        group_ids = [pid for (ptype, pid) in deduped if ptype == PrincipalType.GROUP]
        known_users = await self.users.ids_existing_in_org(principal.organization_id, user_ids)
        known_groups = await self.groups.ids_existing_in_org(principal.organization_id, group_ids)
        unknown = [str(pid) for pid in user_ids if pid not in known_users] + [
            str(pid) for pid in group_ids if pid not in known_groups
        ]
        if unknown:
            raise ValidationFailedError(
                "Some principals don't exist in this organization",
                code="unknown_principals",
                details={"principal_ids": unknown},
            )

        new_grants = [
            DocumentPermission(
                document_id=document.id,
                principal_type=g.principal_type,
                principal_id=g.principal_id,
                permission=g.permission,
                granted_by=principal.user_id,
            )
            for g in deduped.values()
        ]
        await self.docs.replace_grants(document.id, new_grants)
        record_audit(
            self.session,
            action="document.permissions_changed",
            meta=meta,
            organization_id=principal.organization_id,
            actor_id=principal.user_id,
            resource_type="document",
            resource_id=document.id,
            details={"grant_count": len(new_grants), "visibility": document.visibility.value},
        )
        await self.session.commit()
        await self._enqueue_payload_sync(document)
        return document, await self.docs.grants_for(document.id)

    # --- extracted content -----------------------------------------------------------------
    async def list_chunks(
        self, principal: Principal, document_id: uuid.UUID, page: PageParams
    ) -> tuple[Document, list[DocumentChunk], int]:
        """Chunks of the active index version (the extracted text; also the DOCX/Excel preview)."""
        document, _ = await self._get_visible(principal, document_id)
        chunks, total = await self.docs.list_chunks(
            document.id, document.active_index_version, offset=page.offset, limit=page.page_size
        )
        return document, list(chunks), total

    # --- access helpers --------------------------------------------------------------------
    async def _get_visible(
        self, principal: Principal, document_id: uuid.UUID
    ) -> tuple[Document, list[DocumentPermission]]:
        document = await self.docs.get_in_org(principal.organization_id, document_id)
        if document is None:
            raise NotFoundError("Document not found", code="document_not_found")
        grants = await self.docs.grants_for(document.id)
        if not can_view(principal, document, grants):
            # 404, not 403: don't reveal that the document exists.
            raise NotFoundError("Document not found", code="document_not_found")
        return document, grants

    async def _get_manageable(
        self, principal: Principal, document_id: uuid.UUID
    ) -> tuple[Document, list[DocumentPermission]]:
        document, grants = await self._get_visible(principal, document_id)
        if not can_manage(principal, document, grants):
            raise PermissionDeniedError("You don't have permission to manage this document")
        return document, grants
