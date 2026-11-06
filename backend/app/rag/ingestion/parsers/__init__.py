from __future__ import annotations

from pathlib import Path

import structlog

from app.models.enums import FileType
from app.rag.ingestion.parsers.docx import parse_docx
from app.rag.ingestion.parsers.excel import parse_xls, parse_xlsx
from app.rag.ingestion.parsers.pdf import parse_pdf
from app.rag.ingestion.types import ParsedDocument, PermanentIngestionError

log = structlog.get_logger(__name__)


def parse_document(path: Path, file_type: FileType, *, max_pdf_pages: int, max_sheet_rows: int) -> ParsedDocument:
    """Dispatch to the parser for ``file_type``. CPU-bound: call from a worker thread.

    Any parser exception is reported as a permanent failure: parsing is deterministic, so retrying
    the same bytes cannot succeed. (Missing files and memory exhaustion are re-raised as-is.)
    """
    try:
        if file_type is FileType.PDF:
            return parse_pdf(path, max_pages=max_pdf_pages)
        if file_type is FileType.DOCX:
            return parse_docx(path)
        if file_type is FileType.XLSX:
            return parse_xlsx(path, max_rows=max_sheet_rows)
        if file_type is FileType.XLS:
            return parse_xls(path, max_rows=max_sheet_rows)
    except (PermanentIngestionError, FileNotFoundError, MemoryError):
        raise
    except Exception as exc:
        log.warning("parser_error", file_type=file_type.value, error=type(exc).__name__)
        raise PermanentIngestionError(
            f"corrupt_{file_type.value}", f"The {file_type.value.upper()} file could not be read"
        ) from exc
    raise ValueError(f"Unsupported file type: {file_type}")
