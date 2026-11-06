"""DOCX parsing with python-docx: body in document order, heading hierarchy → section paths."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

import docx
from docx.document import Document as DocxDocument
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.rag.ingestion.parsers.common import clean_text, rows_to_markdown
from app.rag.ingestion.types import Block, ParsedDocument, PermanentIngestionError

_HEADING_STYLE = re.compile(r"^(?:Heading|Überschrift|Titre|Título)\s*(\d)$", re.IGNORECASE)


def _heading_level(paragraph: Paragraph) -> int | None:
    style = paragraph.style.name if paragraph.style is not None else ""
    if style == "Title":
        return 0
    m = _HEADING_STYLE.match(style or "")
    if m:
        return int(m.group(1))
    # Explicit outline level (used by documents with custom heading styles).
    ppr = paragraph._p.pPr
    if ppr is not None:
        lvl = ppr.find(qn("w:outlineLvl"))
        if lvl is not None:
            try:
                return int(lvl.get(qn("w:val"))) + 1
            except (TypeError, ValueError):
                return None
    return None


def _table_rows(table: Table) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in table.rows:
        cells: list[str] = []
        previous = None
        for cell in row.cells:
            # Horizontally merged cells are returned repeatedly by python-docx; keep one copy.
            if previous is not None and cell._tc is previous:
                continue
            previous = cell._tc
            cells.append(clean_text(cell.text).replace("\n", " "))
        rows.append(cells)
    return rows


def parse_docx(path: Path) -> ParsedDocument:
    try:
        document: DocxDocument = docx.Document(str(path))
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise PermanentIngestionError("corrupt_docx", "The Word document could not be read") from exc

    headings: list[tuple[int, str]] = []  # stack of (level, title)
    blocks: list[Block] = []
    paragraph_buffer: list[str] = []

    def section() -> str | None:
        return " > ".join(title for _, title in headings) or None

    def flush() -> None:
        if paragraph_buffer:
            blocks.append(Block(kind="text", text="\n\n".join(paragraph_buffer), section_path=section()))
            paragraph_buffer.clear()

    body = document.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            paragraph = Paragraph(child, document)
            text = clean_text(paragraph.text)
            if not text:
                continue
            level = _heading_level(paragraph)
            if level is not None:
                flush()
                while headings and headings[-1][0] >= level:
                    headings.pop()
                headings.append((level, text[:200]))
                # Keep the heading text itself searchable as part of the section's content.
                paragraph_buffer.append(text)
            else:
                paragraph_buffer.append(text)
        elif child.tag == qn("w:tbl"):
            flush()
            markdown = rows_to_markdown(_table_rows(Table(child, document)))  # type: ignore[arg-type]
            if markdown:
                blocks.append(Block(kind="table", text=markdown, section_path=section()))
    flush()

    props = document.core_properties
    metadata = {
        k: v
        for k, v in {
            "title": (props.title or "").strip() or None,
            "author": (props.author or "").strip() or None,
            "subject": (props.subject or "").strip() or None,
            "created": props.created.isoformat() if props.created else None,
            "modified": props.modified.isoformat() if props.modified else None,
        }.items()
        if v
    }
    return ParsedDocument(blocks=blocks, metadata=metadata)
