"""Hyperlink helpers shared by the parsers: find URLs in text, validate targets, de-duplicate.

URLs are returned exactly as written in the document (only surrounding whitespace and sentence
punctuation that is clearly not part of the URL are removed), because answers must quote them verbatim.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from app.rag.ingestion.types import ExtractedLink, LinkType

MAX_URL_CHARS = 2048
MAX_ANCHOR_CHARS = 500
MAX_LINKS_PER_DOCUMENT = 5000

_SCHEMES = {"http", "https", "ftp", "mailto"}
# http(s)/ftp URLs and bare "www." hosts in running text. Stops at whitespace, quotes and angle brackets.
_URL_IN_TEXT = re.compile(r"""(?i)\b(?:(?:https?|ftp)://|www\.)[^\s<>"'`{}|\\^\[\]]+""")
_TRAILING = ".,;:!?*'\""
_WS = re.compile(r"\s+")


def _trim(url: str) -> str:
    """Drop sentence punctuation after a URL, and a closing bracket that has no opening one in it."""
    while url:
        last = url[-1]
        if last in _TRAILING:
            url = url[:-1]
        elif last == ")" and url.count("(") < url.count(")"):
            url = url[:-1]
        else:
            break
    return url


def clean_target(raw: str | None) -> str | None:
    """A link target as stored: http(s)/ftp/mailto only (no javascript:, file:, data:), within length limits."""
    if not raw:
        return None
    url = raw.strip()
    if url.lower().startswith("www."):
        url = "https://" + url
    if len(url) > MAX_URL_CHARS:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in _SCHEMES:
        return None
    if scheme == "mailto":
        return url if len(url) > len("mailto:") else None
    return url if parts.netloc else None


def domain_of(url: str) -> str | None:
    """Lower-case host without "www." (``github.com``); the address domain for mailto links."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme.lower() == "mailto":
        address = parts.path.split("?")[0]
        if "@" not in address:
            return None
        return address.rsplit("@", 1)[-1].lower() or None
    host = (parts.hostname or "").lower()
    return host.removeprefix("www.") or None


def clean_anchor(text: str | None) -> str | None:
    if not text:
        return None
    text = _WS.sub(" ", text).strip()
    return text[:MAX_ANCHOR_CHARS] or None


def _same_as_url(anchor: str, url: str) -> bool:
    def bare(s: str) -> str:
        return re.sub(r"^(?:https?://)?(?:www\.)?", "", s.strip().lower()).rstrip("/")

    return bare(anchor) == bare(url)


def make_link(
    target: str | None,
    *,
    anchor: str | None = None,
    embedded: bool,
    page: int | None = None,
    sheet: str | None = None,
    cell: str | None = None,
    section_path: str | None = None,
) -> ExtractedLink | None:
    """Build a link; ``embedded`` means the target came from link markup rather than visible text."""
    url = clean_target(target)
    if url is None:
        return None
    anchor = clean_anchor(anchor)
    if anchor is not None and _same_as_url(anchor, url):
        anchor = None  # the anchor is just the URL itself
    link_type: LinkType
    if url.lower().startswith("mailto:"):
        link_type = "email"
    elif embedded and anchor is not None:
        link_type = "hyperlink"
    else:
        link_type = "url"
    return ExtractedLink(
        url=url,
        link_type=link_type,
        anchor_text=anchor,
        page=page,
        sheet=sheet,
        cell=cell,
        section_path=(section_path or None) and section_path[:1000],
    )


def urls_in_text(text: str) -> list[str]:
    """URLs written out in ``text``, in order (as written; "www." hosts are not given a scheme here)."""
    found: list[str] = []
    for match in _URL_IN_TEXT.finditer(text):
        url = _trim(match.group(0))
        if len(url) > len("www.") and url not in found:
            found.append(url)
    return found


def text_links(
    text: str,
    *,
    page: int | None = None,
    sheet: str | None = None,
    cell: str | None = None,
    section_path: str | None = None,
) -> list[ExtractedLink]:
    links = []
    for url in urls_in_text(text):
        link = make_link(url, embedded=False, page=page, sheet=sheet, cell=cell, section_path=section_path)
        if link is not None:
            links.append(link)
    return links


def dedupe(links: list[ExtractedLink]) -> list[ExtractedLink]:
    """One link per (URL, location). An embedded hyperlink with anchor text wins over the same URL as text."""
    best: dict[tuple[object, ...], ExtractedLink] = {}
    rank = {"hyperlink": 2, "email": 1, "url": 0}
    for link in links:
        key = (link.url, link.page, link.sheet, link.cell, link.section_path)
        current = best.get(key)
        if current is None or rank[link.link_type] > rank[current.link_type]:
            best[key] = link
    return list(best.values())[:MAX_LINKS_PER_DOCUMENT]


def shown_in(url: str, text: str) -> bool:
    """Whether ``url`` is already written out in ``text`` (also as a bare "www." host)."""
    return url in text or (url.startswith("https://www.") and url.removeprefix("https://") in text)


def hidden_targets_note(links: list[ExtractedLink], heading: str) -> str:
    """Lines that make hidden link targets part of the indexed text ("GitHub Profile: https://...")."""
    lines = [f"- {link.anchor_text}: {link.url}" if link.anchor_text else f"- {link.url}" for link in links]
    return f"{heading}\n" + "\n".join(lines) if lines else ""
