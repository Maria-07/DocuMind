"""Upload validation: extension allow-list *and* content sniffing (never trust the client's MIME type)."""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePath

from app.models.enums import FileType

MIME_TYPES: dict[FileType, str] = {
    FileType.PDF: "application/pdf",
    FileType.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    FileType.XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    FileType.XLS: "application/vnd.ms-excel",
}

_EXTENSIONS: dict[str, FileType] = {f".{ft.value}": ft for ft in FileType}
_OLE2_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")
_OOXML_MAIN_PART = {FileType.DOCX: "word/document.xml", FileType.XLSX: "xl/workbook.xml"}
_MAX_ZIP_ENTRIES = 10_000


class FileValidationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ValidatedFile:
    file_type: FileType
    mime_type: str
    extension: str


def detect_file_type(filename: str) -> FileType:
    ext = PurePath(filename).suffix.lower()
    try:
        return _EXTENSIONS[ext]
    except KeyError:
        allowed = ", ".join(sorted(_EXTENSIONS))
        raise FileValidationError(
            "unsupported_file_type", f"Unsupported file type '{ext or '(none)'}'. Allowed: {allowed}"
        ) from None


def validate_content(path: Path, file_type: FileType, *, max_uncompressed_bytes: int) -> ValidatedFile:
    """Check that the bytes on disk really are the declared type. Raises FileValidationError."""
    with path.open("rb") as fh:
        head = fh.read(1024)

    if not head:
        raise FileValidationError("empty_file", "File is empty")

    if file_type is FileType.PDF:
        if b"%PDF-" not in head:
            raise FileValidationError("content_mismatch", "File content is not a PDF")
    elif file_type is FileType.XLS:
        if not head.startswith(_OLE2_SIGNATURE):
            raise FileValidationError("content_mismatch", "File content is not a legacy Excel (.xls) workbook")
    else:
        _validate_ooxml(path, file_type, max_uncompressed_bytes)

    return ValidatedFile(file_type=file_type, mime_type=MIME_TYPES[file_type], extension=f".{file_type.value}")


def _validate_ooxml(path: Path, file_type: FileType, max_uncompressed_bytes: int) -> None:
    if not zipfile.is_zipfile(path):
        raise FileValidationError("content_mismatch", f"File content is not a valid .{file_type.value} file")
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
    except zipfile.BadZipFile as exc:
        raise FileValidationError("corrupt_file", "File is corrupt") from exc

    if len(infos) > _MAX_ZIP_ENTRIES:
        raise FileValidationError("archive_too_complex", "File contains too many internal parts")
    # Zip-bomb guard: declared uncompressed size (the parser enforces actual sizes while reading).
    if sum(info.file_size for info in infos) > max_uncompressed_bytes:
        raise FileValidationError("archive_too_large", "File expands beyond the allowed uncompressed size")

    names = {info.filename for info in infos}
    if "[Content_Types].xml" not in names or _OOXML_MAIN_PART[file_type] not in names:
        raise FileValidationError("content_mismatch", f"File content is not a valid .{file_type.value} file")
    if any(name.lower().endswith("vbaproject.bin") for name in names):
        raise FileValidationError("macros_not_allowed", "Macro-enabled documents are not accepted")
