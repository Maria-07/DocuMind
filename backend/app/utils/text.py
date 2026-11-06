from __future__ import annotations

import re
import unicodedata
from pathlib import PurePath

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def slugify(value: str, max_length: int = 60) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_RE.sub("-", normalized.lower()).strip("-")
    return slug[:max_length].strip("-") or "org"


def sanitize_filename(filename: str | None, max_length: int = 255) -> str:
    """Keep only the base name (browsers may send paths), strip control characters."""
    raw = (filename or "").replace("\\", "/")
    name = PurePath(raw).name
    name = _CONTROL_RE.sub("", name).strip()
    if len(name) > max_length:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: max_length - len(ext) - 1] + dot + ext) if dot else name[:max_length]
    return name or "unnamed"


def escape_like(value: str) -> str:
    """Escape LIKE/ILIKE wildcards in user input."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
