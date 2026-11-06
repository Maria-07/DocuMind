"""PDF parsing with pdfplumber (MIT): page text in reading order plus tables as Markdown."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pdfplumber
from pdfminer.pdfdocument import PDFEncryptionError, PDFPasswordIncorrect

from app.rag.ingestion.parsers.common import clean_text, rows_to_markdown
from app.rag.ingestion.types import Block, ParsedDocument, PermanentIngestionError

# Pages with less extractable text than this are probably scanned images.
_LOW_TEXT_CHARS = 25
_PDF_DATE = re.compile(r"D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?")


def _pdf_date(value: Any) -> str | None:
    if isinstance(value, bytes):
        value = value.decode("latin-1", "ignore")
    if not isinstance(value, str):
        return None
    m = _PDF_DATE.match(value)
    if not m:
        return None
    parts = [int(p) if p else d for p, d in zip(m.groups(), (0, 1, 1, 0, 0, 0), strict=True)]
    try:
        return datetime(*parts).isoformat()  # type: ignore[arg-type]
    except ValueError:
        return None


def _meta_str(value: Any) -> str | None:
    if isinstance(value, bytes):
        value = value.decode("utf-8", "ignore")
    if isinstance(value, str) and value.strip():
        return value.strip()[:500]
    return None


def _is_encryption_error(exc: BaseException) -> bool:
    # pdfplumber wraps pdfminer errors (PdfminerException), so inspect the whole cause chain.
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, PDFPasswordIncorrect | PDFEncryptionError):
            return True
        if any(isinstance(a, PDFPasswordIncorrect | PDFEncryptionError) for a in getattr(current, "args", ())):
            return True
        current = current.__cause__ or current.__context__
    return False


def parse_pdf(path: Path, *, max_pages: int) -> ParsedDocument:
    try:
        return _parse(path, max_pages)
    except PermanentIngestionError:
        raise
    except Exception as exc:  # pdfminer raises many exception types for malformed files
        if _is_encryption_error(exc):
            raise PermanentIngestionError("encrypted_pdf", "The PDF is password-protected") from exc
        raise PermanentIngestionError("corrupt_pdf", "The PDF could not be read") from exc


def _parse(path: Path, max_pages: int) -> ParsedDocument:
    blocks: list[Block] = []
    warnings: list[str] = []
    low_text_pages: list[int] = []

    with pdfplumber.open(path) as pdf:
        info = pdf.metadata or {}
        metadata = {
            k: v
            for k, v in {
                "title": _meta_str(info.get("Title")),
                "author": _meta_str(info.get("Author")),
                "subject": _meta_str(info.get("Subject")),
                "created": _pdf_date(info.get("CreationDate")),
                "modified": _pdf_date(info.get("ModDate")),
            }.items()
            if v
        }
        page_count = len(pdf.pages)
        if page_count > max_pages:
            warnings.append(f"Only the first {max_pages} of {page_count} pages were indexed")

        for number, page in enumerate(pdf.pages[:max_pages], start=1):
            try:
                tables = page.find_tables()
                text_page = page
                for table in tables:
                    text_page = text_page.outside_bbox(table.bbox, strict=False)
                text = clean_text(text_page.extract_text() or "")
                table_rows = [t.extract() for t in tables]
            except Exception:  # one broken page must not sink the whole document
                warnings.append(f"Page {number} could not be parsed")
                continue
            finally:
                page.flush_cache()  # pdfplumber caches layout objects per page; free them

            if text:
                blocks.append(Block(kind="text", text=text, page=number))
            for rows in table_rows:
                markdown = rows_to_markdown(rows)
                if markdown:
                    blocks.append(Block(kind="table", text=markdown, page=number))
            if len(text) < _LOW_TEXT_CHARS and not any(table_rows):
                low_text_pages.append(number)

    if low_text_pages:
        shown = ", ".join(map(str, low_text_pages[:20])) + ("…" if len(low_text_pages) > 20 else "")
        warnings.append(f"{len(low_text_pages)} page(s) have little or no extractable text (scanned?): {shown}")
    return ParsedDocument(blocks=blocks, metadata=metadata, page_count=page_count, warnings=warnings)
