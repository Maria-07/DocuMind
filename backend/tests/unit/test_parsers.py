from __future__ import annotations

from pathlib import Path

import pytest

from app.models.enums import FileType
from app.rag.ingestion.parsers import parse_document
from app.rag.ingestion.parsers.common import cell_to_str, rows_to_markdown
from app.rag.ingestion.types import PermanentIngestionError
from tests.documents import build_docx, build_pdf, build_xls, build_xlsx, write


def parse(path: Path, file_type: FileType, **limits: int):  # type: ignore[no-untyped-def]
    return parse_document(
        path,
        file_type,
        max_pdf_pages=limits.get("max_pdf_pages", 100),
        max_sheet_rows=limits.get("max_sheet_rows", 1000),
    )


# --- PDF -----------------------------------------------------------------------------------


def test_pdf_pages_tables_and_metadata(tmp_path: Path) -> None:
    data = build_pdf(
        ["Revenue grew in EMEA during the first quarter.", "Second page discusses the hiring plan."],
        table=[["Region", "Revenue"], ["EMEA", "1.2M"], ["APAC", "0.8M"]],
    )
    parsed = parse(write(tmp_path, "r.pdf", data), FileType.PDF)

    assert parsed.page_count == 2
    assert parsed.metadata["title"] == "Quarterly Report"
    assert parsed.metadata["author"] == "Finance Team"
    assert parsed.metadata["created"].startswith("2026-03-01")

    text_blocks = [b for b in parsed.blocks if b.kind == "text"]
    assert [b.page for b in text_blocks] == [1, 2]
    assert "Revenue grew in EMEA" in text_blocks[0].text
    assert "hiring plan" in text_blocks[1].text

    tables = [b for b in parsed.blocks if b.kind == "table"]
    assert len(tables) == 1 and tables[0].page == 1
    assert tables[0].text.splitlines()[0] == "| Region | Revenue |"
    assert "| EMEA | 1.2M |" in tables[0].text
    # Table content is not duplicated into the page's prose.
    assert "1.2M" not in text_blocks[0].text


def test_pdf_flags_pages_without_text(tmp_path: Path) -> None:
    parsed = parse(
        write(tmp_path, "s.pdf", build_pdf(["Real text on the first page. " * 3], blank_pages=2)), FileType.PDF
    )
    assert parsed.page_count == 3
    assert any("2 page(s) have little or no extractable text" in w for w in parsed.warnings)


def test_pdf_page_limit(tmp_path: Path) -> None:
    data = build_pdf([f"Page number {i} content here." for i in range(1, 6)])
    parsed = parse(write(tmp_path, "l.pdf", data), FileType.PDF, max_pdf_pages=2)
    assert {b.page for b in parsed.blocks} == {1, 2}
    assert any("first 2 of 5 pages" in w for w in parsed.warnings)


def test_encrypted_pdf_is_permanent_failure(tmp_path: Path) -> None:
    with pytest.raises(PermanentIngestionError) as exc:
        parse(write(tmp_path, "e.pdf", build_pdf(password="s3cret")), FileType.PDF)
    assert exc.value.code == "encrypted_pdf"


def test_corrupt_pdf_is_permanent_failure(tmp_path: Path) -> None:
    with pytest.raises(PermanentIngestionError) as exc:
        parse(write(tmp_path, "c.pdf", b"%PDF-1.4\n garbage without objects"), FileType.PDF)
    assert exc.value.code == "corrupt_pdf"


# --- DOCX ----------------------------------------------------------------------------------


def test_docx_sections_tables_and_metadata(tmp_path: Path) -> None:
    parsed = parse(write(tmp_path, "p.docx", build_docx()), FileType.DOCX)
    assert parsed.metadata["title"] == "Security Policy"
    assert parsed.metadata["author"] == "CISO Office"

    by_section = {b.section_path: b for b in parsed.blocks if b.kind == "text"}
    passwords = by_section["Security Policy > Access Control > Passwords"]
    assert "14 characters" in passwords.text and "Multi-factor" in passwords.text
    retention = by_section["Security Policy > Data Retention"]
    assert "seven years" in retention.text
    # Sibling heading pops the previous level: Data Retention is not under Access Control.
    assert not any("Access Control > Data Retention" in (s or "") for s in by_section)

    table = next(b for b in parsed.blocks if b.kind == "table")
    assert table.section_path == "Security Policy > Data Retention"
    assert "| Invoices | 10 years |" in table.text


def test_corrupt_docx(tmp_path: Path) -> None:
    with pytest.raises(PermanentIngestionError):
        parse(write(tmp_path, "c.docx", b"PK\x03\x04 not really a docx"), FileType.DOCX)


# --- Excel ---------------------------------------------------------------------------------


def test_xlsx_rows_headers_sheets_and_hidden(tmp_path: Path) -> None:
    parsed = parse(write(tmp_path, "r.xlsx", build_xlsx(rows=4)), FileType.XLSX)
    assert parsed.sheet_names == ["Revenue", "Notes", "Lookup"]
    assert parsed.metadata["title"] == "Revenue 2026"
    assert parsed.metadata["hidden_sheets"] == ["Lookup"]
    assert any("Hidden sheets were indexed: Lookup" in w for w in parsed.warnings)

    revenue = next(b for b in parsed.blocks if b.sheet == "Revenue")
    assert revenue.kind == "rows"
    assert revenue.header == "Region | Quarter | Revenue | Booked"
    assert revenue.rows[0] == (2, "Region: EMEA | Quarter: Q1 | Revenue: 1000.5 | Booked: 2026-01-01")
    assert [n for n, _ in revenue.rows] == [2, 3, 4, 5]


def test_xlsx_row_limit(tmp_path: Path) -> None:
    parsed = parse(write(tmp_path, "big.xlsx", build_xlsx(rows=30)), FileType.XLSX, max_sheet_rows=10)
    revenue = next(b for b in parsed.blocks if b.sheet == "Revenue")
    assert len(revenue.rows) == 10
    assert any("only the first 10 rows" in w for w in parsed.warnings)


def test_xls_types_and_dates(tmp_path: Path) -> None:
    parsed = parse(write(tmp_path, "b.xls", build_xls()), FileType.XLS)
    assert parsed.sheet_names == ["Budget"]
    block = parsed.blocks[0]
    assert block.header == "Department | Budget | Approved | Approved on"
    assert block.rows == [
        (2, "Department: Engineering | Budget: 120000 | Approved: TRUE | Approved on: 2026-02-01"),
        (3, "Department: Sales | Budget: 80000.5 | Approved: FALSE | Approved on: 2026-02-02"),
    ]


def test_corrupt_xls(tmp_path: Path) -> None:
    with pytest.raises(PermanentIngestionError):
        parse(write(tmp_path, "c.xls", bytes.fromhex("D0CF11E0A1B11AE1") + b"\x00" * 100), FileType.XLS)


# --- helpers -------------------------------------------------------------------------------


def test_rows_to_markdown_pads_and_escapes() -> None:
    md = rows_to_markdown([["A", "B|C"], ["1"], [None, None], ["x", "y", "z"]])
    assert md.splitlines() == ["| A | B\\|C |  |", "|---|---|---|", "| 1 |  |  |", "| x | y | z |"]


@pytest.mark.parametrize(("value", "expected"), [(3.0, "3"), (2.5, "2.5"), (True, "TRUE"), (None, ""), ("a\nb", "a b")])
def test_cell_to_str(value: object, expected: str) -> None:
    assert cell_to_str(value) == expected
