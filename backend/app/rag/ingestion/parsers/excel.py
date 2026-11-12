"""Spreadsheet parsing: XLSX via openpyxl, legacy XLS via xlrd.

Each sheet becomes one ``rows`` block: a header line plus ``Header: value`` records per row, so
every chunk is self-describing ("Region: EMEA | Revenue: 1.2M") and cites sheet + row range.

Hyperlinks: a cell whose link target differs from what it shows is indexed as "value (target)", and
every link is also returned as a structured record with its sheet and cell reference.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import ParseError

import openpyxl
import xlrd
from defusedxml import ElementTree
from openpyxl.utils.cell import column_index_from_string, coordinate_from_string, get_column_letter

from app.rag.ingestion.links import dedupe, make_link, shown_in, text_links
from app.rag.ingestion.parsers.common import cell_to_str
from app.rag.ingestion.types import Block, ExtractedLink, ParsedDocument, PermanentIngestionError

_MAX_COLUMNS = 200

# (1-based row number, 0-based column) -> (link target, display text or None)
CellLinks = dict[tuple[int, int], tuple[str, str | None]]

_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
# =HYPERLINK("https://...", "label") with literal string arguments (Excel or LibreOffice separators).
_HYPERLINK_FORMULA = re.compile(r'^\s*=?\s*HYPERLINK\(\s*"([^"]+)"\s*(?:[,;]\s*"([^"]*)"\s*)?\)', re.IGNORECASE)


def _cell_key(ref: str) -> tuple[int, int] | None:
    try:
        column, row = coordinate_from_string(ref.split(":")[0])  # a range links from its top-left cell
        return row, column_index_from_string(column) - 1
    except ValueError:
        return None


def _relationships(archive: zipfile.ZipFile, name: str) -> dict[str, str]:
    try:
        root = ElementTree.fromstring(archive.read(name))
    except KeyError:
        return {}
    return {r.get("Id", ""): r.get("Target", "") for r in root.iter(f"{_PKG_REL}Relationship")}


def _sheet_part(target: str) -> str:
    return target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))


def _scan_sheet(source: Any, rels: dict[str, str]) -> CellLinks:
    links: CellLinks = {}
    for _, element in ElementTree.iterparse(source):
        if element.tag == f"{_MAIN}c":
            formula = element.find(f"{_MAIN}f")
            if formula is not None and formula.text and "HYPERLINK" in formula.text.upper():
                match = _HYPERLINK_FORMULA.match(formula.text)
                key = _cell_key(element.get("r", ""))
                if match and key is not None:
                    links.setdefault(key, (match.group(1), match.group(2)))
            element.clear()  # stream: don't keep the sheet in memory
        elif element.tag == f"{_MAIN}hyperlink":
            url = rels.get(element.get(f"{_REL}id", ""))
            key = _cell_key(element.get("ref", ""))
            if url and key is not None:  # links with only ``location`` point inside the workbook
                links[key] = (url, element.get("display"))
    return links


def xlsx_cell_links(path: Path) -> dict[str, CellLinks]:
    """Hyperlinks per sheet name, read from the workbook XML.

    openpyxl does not expose hyperlinks in read-only mode (the mode that keeps big workbooks cheap), so
    each sheet's XML is streamed once more for ``<hyperlink>`` elements and HYPERLINK formulas. defusedxml
    refuses DTDs and entity tricks (the file is untrusted).
    """
    result: dict[str, CellLinks] = {}
    with zipfile.ZipFile(path) as archive:
        workbook_rels = _relationships(archive, "xl/_rels/workbook.xml.rels")
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        for sheet in workbook.iter(f"{_MAIN}sheet"):
            target = workbook_rels.get(sheet.get(f"{_REL}id", ""), "")
            if not target:
                continue
            part = _sheet_part(target)
            rels_name = posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")
            try:
                source = archive.open(part)
            except KeyError:
                continue
            with source:
                links = _scan_sheet(source, _relationships(archive, rels_name))
            if links:
                result[sheet.get("name", "")] = links
    return result


def xls_cell_links(sheet: Any) -> CellLinks:
    """Web hyperlinks of a legacy XLS sheet (xlrd reads the HLINK records; file and in-workbook links are skipped)."""
    links: CellLinks = {}
    for (row, column), hyperlink in (getattr(sheet, "hyperlink_map", None) or {}).items():
        target = str(getattr(hyperlink, "url_or_path", "") or "").rstrip("\x00")
        if getattr(hyperlink, "type", None) == "url" and target:
            desc = str(getattr(hyperlink, "desc", "") or "").rstrip("\x00") or None
            links[(row + 1, column)] = (target, desc)
    return links


def _row_links(
    sheet: str, row_number: int, cells: list[str], cell_links: CellLinks, out: list[ExtractedLink], *, inline: bool
) -> None:
    """Record the links in one row; with ``inline``, write each hidden target after the cell's value."""
    for i, value in enumerate(cells):
        ref = f"{get_column_letter(i + 1)}{row_number}"
        found = text_links(value, sheet=sheet, cell=ref)
        target = cell_links.get((row_number, i))
        if target is not None:
            url, display = target
            link = make_link(url, anchor=value or display, embedded=True, sheet=sheet, cell=ref)
            if link is not None:
                found.insert(0, link)
                if inline and not shown_in(link.url, value):
                    cells[i] = f"{value} ({link.url})" if value else link.url
        out.extend(found)


def _sheet_block(
    name: str,
    rows: Iterable[tuple[int, list[Any]]],
    max_rows: int,
    warnings: list[str],
    links: list[ExtractedLink] | None = None,
    cell_links: CellLinks | None = None,
) -> Block | None:
    header: list[str] | None = None
    records: list[tuple[int, str]] = []
    truncated = False
    out: list[ExtractedLink] = links if links is not None else []
    targets: CellLinks = cell_links or {}
    for row_number, values in rows:
        cells = [cell_to_str(v) for v in values[:_MAX_COLUMNS]]
        if targets:
            # Header cells become labels in every record, so their targets are recorded but not inlined.
            _row_links(name, row_number, cells, targets, out, inline=header is not None)
        elif any("://" in c or "www." in c for c in cells):
            _row_links(name, row_number, cells, targets, out, inline=False)
        if not any(cells):
            continue
        if header is None:
            # First non-empty row is the header; unnamed columns are labelled by position.
            header = [c or f"Column {i + 1}" for i, c in enumerate(cells)]
            continue
        if len(records) >= max_rows:
            truncated = True
            break
        parts = []
        for i, value in enumerate(cells):
            if value:
                label = header[i] if i < len(header) else f"Column {i + 1}"
                parts.append(f"{label}: {value}")
        records.append((row_number, " | ".join(parts)))

    if truncated:
        warnings.append(f"Sheet '{name}': only the first {max_rows} rows were indexed")
    if header is None:
        return None
    if not records:
        # Header-only sheet: keep it as text so the sheet is still findable.
        return Block(kind="text", text=" | ".join(header), sheet=name)
    return Block(kind="rows", sheet=name, header=" | ".join(header), rows=records)


def parse_xlsx(path: Path, *, max_rows: int) -> ParsedDocument:
    try:
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except (zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
        raise PermanentIngestionError("corrupt_xlsx", "The Excel workbook could not be read") from exc

    blocks: list[Block] = []
    warnings: list[str] = []
    hidden: list[str] = []
    links: list[ExtractedLink] = []
    try:
        sheet_links = xlsx_cell_links(path)
    except (zipfile.BadZipFile, KeyError, ParseError, ValueError):
        sheet_links = {}
        warnings.append("Hyperlinks could not be read from this workbook")
    try:
        sheet_names = list(workbook.sheetnames)
        for ws in workbook.worksheets:
            if getattr(ws, "sheet_state", "visible") != "visible":
                hidden.append(ws.title)

            def rows(ws: Any = ws) -> Iterator[tuple[int, list[Any]]]:
                for number, row in enumerate(ws.iter_rows(values_only=True), start=1):
                    yield number, list(row)

            block = _sheet_block(ws.title, rows(), max_rows, warnings, links, sheet_links.get(ws.title))
            if block is not None:
                blocks.append(block)
        props = workbook.properties
        metadata = {
            k: v
            for k, v in {
                "title": (props.title or "").strip() or None,
                "author": (props.creator or "").strip() or None,
                "created": props.created.isoformat() if props.created else None,
                "modified": props.modified.isoformat() if props.modified else None,
                "hidden_sheets": hidden or None,
            }.items()
            if v
        }
    finally:
        workbook.close()
    if hidden:
        warnings.append(f"Hidden sheets were indexed: {', '.join(hidden)}")
    return ParsedDocument(
        blocks=blocks, metadata=metadata, sheet_names=sheet_names, warnings=warnings, links=dedupe(links)
    )


def parse_xls(path: Path, *, max_rows: int) -> ParsedDocument:
    try:
        book = xlrd.open_workbook(str(path), on_demand=True)
    except (xlrd.XLRDError, OSError, ValueError, IndexError) as exc:
        raise PermanentIngestionError("corrupt_xls", "The Excel workbook could not be read") from exc

    blocks: list[Block] = []
    warnings: list[str] = []
    links: list[ExtractedLink] = []
    try:
        sheet_names = list(book.sheet_names())
        for index in range(book.nsheets):
            sheet = book.sheet_by_index(index)

            def rows(sheet: Any = sheet) -> Iterator[tuple[int, list[Any]]]:
                for r in range(sheet.nrows):
                    values: list[Any] = []
                    for c in range(min(sheet.ncols, _MAX_COLUMNS)):
                        cell = sheet.cell(r, c)
                        if cell.ctype == xlrd.XL_CELL_DATE:
                            try:
                                values.append(xlrd.xldate_as_datetime(cell.value, book.datemode))
                            except (ValueError, OverflowError):
                                values.append(cell.value)
                        elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                            values.append(bool(cell.value))
                        elif cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK, xlrd.XL_CELL_ERROR):
                            values.append(None)
                        else:
                            values.append(cell.value)
                    yield r + 1, values

            block = _sheet_block(sheet.name, rows(), max_rows, warnings, links, xls_cell_links(sheet))
            if block is not None:
                blocks.append(block)
            book.unload_sheet(index)
        metadata: dict[str, Any] = {}
        if getattr(book, "user_name", None):
            metadata["author"] = str(book.user_name).strip()[:200]
    finally:
        book.release_resources()
    return ParsedDocument(
        blocks=blocks, metadata=metadata, sheet_names=sheet_names, warnings=warnings, links=dedupe(links)
    )
