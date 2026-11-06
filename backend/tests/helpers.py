"""Builders for minimal-but-valid test files and settings."""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path
from typing import Any

from app.core.config import Settings

TEST_SECRET = "test-secret-key-0123456789-abcdefghijklmnop"


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "test",
        "database_url": os.environ.get("TEST_DATABASE_URL", "postgresql+asyncpg://unused:unused@localhost/unused"),
        "jwt_secret_key": TEST_SECRET,
        "cookie_secure": False,  # the test client talks plain HTTP
        "log_json": False,
        "log_level": "WARNING",
        "rate_limit_enabled": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def pdf_bytes(marker: str = "") -> bytes:
    return (
        b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\n"
        + f"% {marker}\n".encode()
        + b"trailer << /Root 1 0 R >>\n%%EOF\n"
    )


def _ooxml(main_part: str, marker: str, extra: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        zf.writestr(main_part, f"<root>{marker}</root>")
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()


def docx_bytes(marker: str = "", extra: dict[str, bytes] | None = None) -> bytes:
    return _ooxml("word/document.xml", marker, extra)


def xlsx_bytes(marker: str = "", extra: dict[str, bytes] | None = None) -> bytes:
    return _ooxml("xl/workbook.xml", marker, extra)


def xls_bytes(marker: str = "") -> bytes:
    return bytes.fromhex("D0CF11E0A1B11AE1") + b"\x00" * 504 + marker.encode()


def write(tmp: Path, name: str, data: bytes) -> Path:
    path = tmp / name
    path.write_bytes(data)
    return path
