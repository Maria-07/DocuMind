"""DOCX parsing with python-docx: body in document order, heading hierarchy → section paths."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

import docx
from docx.document import Document as DocxDocument
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.hyperlink import Hyperlink
from docx.text.paragraph import Paragraph

from app.rag.ingestion.links import dedupe, make_link, shown_in, text_links
from app.rag.ingestion.parsers.common import clean_text, rows_to_markdown
from app.rag.ingestion.types import Block, ExtractedLink, ParsedDocument, PermanentIngestionError

_HEADING_STYLE = re.compile(r"^(?:Heading|Überschrift|Titre|Título)\s*(\d)$", re.IGNORECASE)
# Field-code hyperlinks: { HYPERLINK "https://..." } (complex fields) and <w:fldSimple w:instr="HYPERLINK ...">.
_FIELD_HYPERLINK = re.compile(r'HYPERLINK\s+"([^"]+)"', re.IGNORECASE)


def _render(paragraph: Paragraph) -> tuple[str, list[ExtractedLink]]:
    """Paragraph text as python-docx reads it, with each hyperlink target written after its anchor text.

    "GitHub Profile" linking to https://github.com/jane becomes "GitHub Profile (https://github.com/jane)",
    so the target is searchable and quotable. Internal links (bookmarks) are kept as plain text.
    """
    parts: list[str] = []
    links: list[ExtractedLink] = []
    for item in paragraph.iter_inner_content():
        if not isinstance(item, Hyperlink):
            parts.append(item.text)
            continue
        text = item.text
        link = make_link(item.url, anchor=text, embedded=True) if item.address else None
        if link is None:
            parts.append(text)
            continue
        links.append(link)
        parts.append(f"{text} ({link.url})" if link.anchor_text else text)
    rendered = "".join(parts)

    instructions = [t.text or "" for t in paragraph._p.iter(qn("w:instrText"))]
    instructions += [f.get(qn("w:instr")) or "" for f in paragraph._p.iter(qn("w:fldSimple"))]
    for target in _FIELD_HYPERLINK.findall(" ".join(instructions)):
        link = make_link(target, anchor=clean_text(rendered) or None, embedded=True)
        if link is not None and not shown_in(link.url, rendered):
            links.append(link)
            rendered = f"{rendered} ({link.url})"
    links += text_links(rendered)
    return rendered, links


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


def _table_rows(table: Table, links: list[ExtractedLink]) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in table.rows:
        cells: list[str] = []
        previous = None
        for cell in row.cells:
            # Horizontally merged cells are returned repeatedly by python-docx; keep one copy.
            if previous is not None and cell._tc is previous:
                continue
            previous = cell._tc
            texts = []
            for paragraph in cell.paragraphs:
                text, found = _render(paragraph)
                texts.append(text)
                links.extend(found)
            cells.append(clean_text("\n".join(texts)).replace("\n", " "))
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
    links: list[ExtractedLink] = []

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
            rendered, found = _render(paragraph)
            text = clean_text(rendered)
            if not text:
                continue
            level = _heading_level(paragraph)
            if level is not None:
                flush()
                while headings and headings[-1][0] >= level:
                    headings.pop()
                # The section title is the visible heading text, without link targets.
                headings.append((level, clean_text(paragraph.text)[:200] or text[:200]))
                # Keep the heading text itself searchable as part of the section's content.
                paragraph_buffer.append(text)
            else:
                paragraph_buffer.append(text)
            for link in found:
                link.section_path = section()
            links.extend(found)
        elif child.tag == qn("w:tbl"):
            flush()
            found = []
            markdown = rows_to_markdown(_table_rows(Table(child, document), found))
            for link in found:
                link.section_path = section()
            links.extend(found)
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
    return ParsedDocument(blocks=blocks, metadata=metadata, links=dedupe(links))
