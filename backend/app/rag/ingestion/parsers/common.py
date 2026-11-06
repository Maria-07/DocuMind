from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import Any

_MULTISPACE = re.compile(r"[ \t\u00a0]+")  # spaces, tabs, no-break spaces
_MANY_NEWLINES = re.compile(r"\n{3,}")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(text: str) -> str:
    text = _CONTROL.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    lines = [_MULTISPACE.sub(" ", line).strip() for line in text.split("\n")]
    return _MANY_NEWLINES.sub("\n\n", "\n".join(lines)).strip()


def cell_to_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() and abs(value) < 1e15 else repr(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ") if value.time() != time(0) else value.date().isoformat()
    if isinstance(value, date | time):
        return value.isoformat()
    return clean_text(str(value)).replace("\n", " ")


def _md_cell(value: Any) -> str:
    return cell_to_str(value).replace("|", "\\|")


def rows_to_markdown(rows: list[list[Any]]) -> str:
    """Render a table as Markdown; the first non-empty row is treated as the header."""
    cleaned = [[_md_cell(c) for c in row] for row in rows if row and any(cell_to_str(c) for c in row)]
    if not cleaned:
        return ""
    width = max(len(r) for r in cleaned)
    cleaned = [r + [""] * (width - len(r)) for r in cleaned]
    header, *body = cleaned
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(lines)
