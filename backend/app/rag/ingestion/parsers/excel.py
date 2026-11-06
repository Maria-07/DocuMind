"""Spreadsheet parsing: XLSX via openpyxl, legacy XLS via xlrd.

Each sheet becomes one ``rows`` block: a header line plus ``Header: value`` records per row, so
every chunk is self-describing ("Region: EMEA | Revenue: 1.2M") and cites sheet + row range.
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import openpyxl
import xlrd

from app.rag.ingestion.parsers.common import cell_to_str
from app.rag.ingestion.types import Block, ParsedDocument, PermanentIngestionError

_MAX_COLUMNS = 200


def _sheet_block(name: str, rows: Iterable[tuple[int, list[Any]]], max_rows: int, warnings: list[str]) -> Block | None:
    header: list[str] | None = None
    records: list[tuple[int, str]] = []
    truncated = False
    for row_number, values in rows:
        cells = [cell_to_str(v) for v in values[:_MAX_COLUMNS]]
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
    try:
        sheet_names = list(workbook.sheetnames)
        for ws in workbook.worksheets:
            if getattr(ws, "sheet_state", "visible") != "visible":
                hidden.append(ws.title)

            def rows(ws: Any = ws) -> Iterator[tuple[int, list[Any]]]:
                for number, row in enumerate(ws.iter_rows(values_only=True), start=1):
                    yield number, list(row)

            block = _sheet_block(ws.title, rows(), max_rows, warnings)
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
    return ParsedDocument(blocks=blocks, metadata=metadata, sheet_names=sheet_names, warnings=warnings)


def parse_xls(path: Path, *, max_rows: int) -> ParsedDocument:
    try:
        book = xlrd.open_workbook(str(path), on_demand=True)
    except (xlrd.XLRDError, OSError, ValueError, IndexError) as exc:
        raise PermanentIngestionError("corrupt_xls", "The Excel workbook could not be read") from exc

    blocks: list[Block] = []
    warnings: list[str] = []
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

            block = _sheet_block(sheet.name, rows(), max_rows, warnings)
            if block is not None:
                blocks.append(block)
            book.unload_sheet(index)
        metadata: dict[str, Any] = {}
        if getattr(book, "user_name", None):
            metadata["author"] = str(book.user_name).strip()[:200]
    finally:
        book.release_resources()
    return ParsedDocument(blocks=blocks, metadata=metadata, sheet_names=sheet_names, warnings=warnings)
