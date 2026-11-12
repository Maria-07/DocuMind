"""PDF parsing with pdfplumber (MIT): page text in reading order plus tables as Markdown."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pdfplumber
from pdfminer.pdfdocument import PDFEncryptionError, PDFPasswordIncorrect

from app.rag.ingestion.links import dedupe, hidden_targets_note, make_link, shown_in, text_links
from app.rag.ingestion.parsers.common import clean_text, rows_to_markdown
from app.rag.ingestion.types import Block, ExtractedLink, ParsedDocument, PermanentIngestionError

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


_ANCHOR_SLACK = 2.0  # points: words whose centre is this close to a link box belong to it


def _page_links(page: Any, number: int) -> list[ExtractedLink]:
    """Link annotations (URI actions) on a page, with the words under each link box as anchor text."""
    annotations = page.hyperlinks
    if not annotations:
        return []
    words = page.extract_words()
    links: list[ExtractedLink] = []
    for annot in annotations:
        x0, x1, top, bottom = annot["x0"], annot["x1"], annot["top"], annot["bottom"]
        anchor = " ".join(
            w["text"]
            for w in words
            if x0 - _ANCHOR_SLACK <= (w["x0"] + w["x1"]) / 2 <= x1 + _ANCHOR_SLACK
            and top - _ANCHOR_SLACK <= (w["top"] + w["bottom"]) / 2 <= bottom + _ANCHOR_SLACK
        )
        link = make_link(annot.get("uri"), anchor=anchor, embedded=True, page=number)
        if link is not None:
            links.append(link)
    return links


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
    links: list[ExtractedLink] = []

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
                embedded = _page_links(page, number)
            except Exception:  # one broken page must not sink the whole document
                warnings.append(f"Page {number} could not be parsed")
                continue
            finally:
                page.flush_cache()  # pdfplumber caches layout objects per page; free them

            markdowns = [m for m in (rows_to_markdown(rows) for rows in table_rows) if m]
            visible = "\n".join([text, *markdowns])
            page_links = dedupe(embedded + text_links(visible, page=number))
            links.extend(page_links)
            # Link targets hidden behind text ("GitHub Profile") become part of the page's indexed text, so
            # search finds them and answers can quote and cite the exact URL.
            hidden = [lk for lk in page_links if not shown_in(lk.url, visible)]
            note = hidden_targets_note(hidden, "Links on this page:")
            page_text = f"{text}\n\n{note}" if text and note else text or note
            if page_text:
                blocks.append(Block(kind="text", text=page_text, page=number))
            for markdown in markdowns:
                blocks.append(Block(kind="table", text=markdown, page=number))
            if len(text) < _LOW_TEXT_CHARS and not any(table_rows):
                low_text_pages.append(number)

    if low_text_pages:
        shown = ", ".join(map(str, low_text_pages[:20])) + ("…" if len(low_text_pages) > 20 else "")
        warnings.append(f"{len(low_text_pages)} page(s) have little or no extractable text (scanned?): {shown}")
    return ParsedDocument(
        blocks=blocks, metadata=metadata, page_count=page_count, warnings=warnings, links=dedupe(links)
    )
