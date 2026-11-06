"""Format-independent representation of a parsed document."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

BlockKind = Literal["text", "table", "rows"]


@dataclass(slots=True)
class Block:
    """A contiguous piece of content with its source location (used for citations)."""

    kind: BlockKind
    text: str = ""
    page: int | None = None  # 1-based (PDF)
    section_path: str | None = None  # "Security > Access Control" (DOCX headings)
    sheet: str | None = None  # Excel sheet name
    # For kind == "rows": the header line and (row_number, line) pairs, packed into chunks later.
    header: str | None = None
    rows: list[tuple[int, str]] = field(default_factory=list)


@dataclass(slots=True)
class ParsedDocument:
    blocks: list[Block]
    metadata: dict[str, Any] = field(default_factory=dict)
    page_count: int | None = None
    sheet_names: list[str] | None = None
    warnings: list[str] = field(default_factory=list)


class IngestionError(Exception):
    """Base class for ingestion failures."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class PermanentIngestionError(IngestionError):
    """The document itself is the problem (corrupt, encrypted, no text): retrying won't help."""
