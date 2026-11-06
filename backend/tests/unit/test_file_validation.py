from __future__ import annotations

from pathlib import Path

import pytest

from app.models.enums import FileType
from app.services.file_validation import FileValidationError, detect_file_type, validate_content
from tests.helpers import docx_bytes, pdf_bytes, write, xls_bytes, xlsx_bytes

LIMIT = 50 * 1024 * 1024


@pytest.mark.parametrize(
    ("name", "expected"),
    [("a.pdf", FileType.PDF), ("B.DOCX", FileType.DOCX), ("c.xlsx", FileType.XLSX), ("d.Xls", FileType.XLS)],
)
def test_detect_file_type(name: str, expected: FileType) -> None:
    assert detect_file_type(name) is expected


@pytest.mark.parametrize("name", ["evil.exe", "notes.txt", "archive.zip", "noext", "macro.docm", "x.pdf.exe"])
def test_detect_rejects_unsupported(name: str) -> None:
    with pytest.raises(FileValidationError) as exc:
        detect_file_type(name)
    assert exc.value.code == "unsupported_file_type"


@pytest.mark.parametrize(
    ("name", "data", "file_type"),
    [
        ("a.pdf", pdf_bytes(), FileType.PDF),
        ("a.docx", docx_bytes(), FileType.DOCX),
        ("a.xlsx", xlsx_bytes(), FileType.XLSX),
        ("a.xls", xls_bytes(), FileType.XLS),
    ],
)
def test_valid_files_pass(tmp_path: Path, name: str, data: bytes, file_type: FileType) -> None:
    result = validate_content(write(tmp_path, name, data), file_type, max_uncompressed_bytes=LIMIT)
    assert result.file_type is file_type
    assert result.extension == f".{file_type.value}"


@pytest.mark.parametrize(
    ("data", "file_type"),
    [
        (b"MZ\x90\x00 this is an executable", FileType.PDF),  # renamed .exe
        (pdf_bytes(), FileType.DOCX),  # PDF renamed to .docx
        (docx_bytes(), FileType.XLSX),  # DOCX renamed to .xlsx
        (xlsx_bytes(), FileType.XLS),  # XLSX renamed to .xls
        (b"PK\x03\x04garbage", FileType.DOCX),  # broken zip
    ],
)
def test_content_mismatch_rejected(tmp_path: Path, data: bytes, file_type: FileType) -> None:
    with pytest.raises(FileValidationError) as exc:
        validate_content(write(tmp_path, "f", data), file_type, max_uncompressed_bytes=LIMIT)
    assert exc.value.code in {"content_mismatch", "corrupt_file"}


def test_empty_file_rejected(tmp_path: Path) -> None:
    with pytest.raises(FileValidationError) as exc:
        validate_content(write(tmp_path, "e.pdf", b""), FileType.PDF, max_uncompressed_bytes=LIMIT)
    assert exc.value.code == "empty_file"


def test_macro_enabled_document_rejected(tmp_path: Path) -> None:
    data = docx_bytes(extra={"word/vbaProject.bin": b"\x00" * 10})
    with pytest.raises(FileValidationError) as exc:
        validate_content(write(tmp_path, "m.docx", data), FileType.DOCX, max_uncompressed_bytes=LIMIT)
    assert exc.value.code == "macros_not_allowed"


def test_zip_bomb_guard(tmp_path: Path) -> None:
    data = xlsx_bytes(extra={"xl/huge.xml": b"0" * 200_000})  # compresses to almost nothing
    with pytest.raises(FileValidationError) as exc:
        validate_content(write(tmp_path, "b.xlsx", data), FileType.XLSX, max_uncompressed_bytes=100_000)
    assert exc.value.code == "archive_too_large"
