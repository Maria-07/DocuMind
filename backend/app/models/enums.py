from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    MANAGER = "manager"
    USER = "user"


class DocumentStatus(StrEnum):
    """Lifecycle: uploaded → (queued) → processing → indexed ("Completed"); failures: processing → (retrying →
    processing) → failed. ``queued`` and ``retrying`` are only used for jobs driven by the n8n workflow."""

    UPLOADED = "uploaded"
    QUEUED = "queued"
    PROCESSING = "processing"
    RETRYING = "retrying"
    INDEXED = "indexed"
    FAILED = "failed"


class DocumentVisibility(StrEnum):
    PRIVATE = "private"  # owner (+ admins) only
    ORGANIZATION = "organization"  # everyone in the organization
    RESTRICTED = "restricted"  # owner + explicit grants (users / groups)


class FileType(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    XLSX = "xlsx"
    XLS = "xls"


class PrincipalType(StrEnum):
    USER = "user"
    GROUP = "group"


class GrantPermission(StrEnum):
    VIEW = "view"
    MANAGE = "manage"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
