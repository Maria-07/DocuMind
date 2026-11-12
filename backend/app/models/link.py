from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class DocumentLink(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A hyperlink found in a document, at one location.

    Versioned like chunks: each re-index writes rows for its ``index_version`` and the finalize step
    removes the other versions, so reprocessing never leaves duplicates. One row per (URL, location):
    the same URL on two pages is two rows, the same URL twice at one location is one.
    """

    __tablename__ = "document_links"
    __table_args__ = (
        UniqueConstraint("document_id", "index_version", "position", name="uq_document_links_position"),
        Index("ix_document_links_document_version", "document_id", "index_version"),
        Index("ix_document_links_org_domain", "organization_id", "domain"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    index_version: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)  # order of appearance in the document
    url: Mapped[str] = mapped_column(Text, nullable=False)  # exactly as in the document
    domain: Mapped[str | None] = mapped_column(String(255))  # "github.com" (no "www."), for exact lookups
    link_type: Mapped[str] = mapped_column(String(16), nullable=False)  # hyperlink | url | email
    anchor_text: Mapped[str | None] = mapped_column(Text)  # visible text of a hidden-target link
    page_number: Mapped[int | None] = mapped_column(Integer)
    sheet_name: Mapped[str | None] = mapped_column(String(255))
    cell_reference: Mapped[str | None] = mapped_column(String(32))
    section_name: Mapped[str | None] = mapped_column(String(1000))
