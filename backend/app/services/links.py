"""Structured hyperlink lookup, alongside vector retrieval.

Questions like "What is the GitHub link in my resume?" are answered from the ``document_links`` table
with exact and keyword matching (domain, anchor text, URL), not from embeddings, so the URL that comes
back is exactly the one in the document. Access rules are the same SQL as document listings.

The matched links are handed to the answer model as one citable source per document ("Hyperlinks in
<title>"), placed before the retrieved passages.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.permissions import Principal
from app.models import Document, DocumentLink
from app.models.enums import FileType
from app.rag.retrieval import RetrievalFilters, RetrievedChunk
from app.repositories.documents import visible_documents_clause
from app.schemas.links import LinkRead

LINK_SOURCE_KIND = "links"
_NAMESPACE = uuid.UUID("0c4d5a3e-8f1b-4b6e-9d2a-7e5f3c1a9b80")

# Platform names people use in questions -> the domains their links live on.
PLATFORMS: dict[str, tuple[str, ...]] = {
    "github": ("github.com", "github.io"),
    "gitlab": ("gitlab.com",),
    "bitbucket": ("bitbucket.org",),
    "linkedin": ("linkedin.com",),
    "twitter": ("twitter.com", "x.com"),
    "facebook": ("facebook.com", "fb.com"),
    "instagram": ("instagram.com",),
    "youtube": ("youtube.com", "youtu.be"),
    "medium": ("medium.com",),
    "behance": ("behance.net",),
    "dribbble": ("dribbble.com",),
    "kaggle": ("kaggle.com",),
    "stackoverflow": ("stackoverflow.com",),
    "stack overflow": ("stackoverflow.com",),
    "leetcode": ("leetcode.com",),
    "orcid": ("orcid.org",),
    "hugging face": ("huggingface.co",),
    "huggingface": ("huggingface.co",),
}
# Words that unambiguously ask for links. "Profile", "site" and "portfolio" also occur in other questions
# ("risk profile"), so they only count when a link actually matches the question's other words.
_STRONG_LINK_WORDS = re.compile(
    r"\b(?:links?|urls?|hyperlinks?|websites?|web\s?sites?|web\s?pages?|homepages?|web\s+address(?:es)?)\b",
    re.IGNORECASE,
)
_WEAK_LINK_WORDS = re.compile(r"\b(?:sites?|portfolios?|profiles?)\b", re.IGNORECASE)
# "Show me all links", "List the URLs", "Which websites are referenced", "What links are there" — but not
# "What is the portfolio link?" (one specific link).
_LIST_ALL = re.compile(
    r"\b(?:all|every|list)\b(?:\W+\w+){0,3}?\W+(?:links?|urls?|hyperlinks?|websites?|sites?)\b"
    r"|\b(?:which|what)\s+(?:\w+\s+)?(?:links|urls|hyperlinks|websites|web\s?sites|sites)\b",
    re.IGNORECASE,
)
_DOMAIN_IN_TEXT = re.compile(r"\b((?:[a-z0-9-]+\.)+(?:com|org|net|io|dev|ai|co|app|me|edu|gov|info|xyz))\b", re.I)
_QUOTED = re.compile(r"[\"“”]([^\"“”]{2,80})[\"“”]")
_WORD = re.compile(r"[a-z0-9][a-z0-9+.#-]*", re.IGNORECASE)
# Words that say "this is a link question" or are filler; they never narrow the match.
_STOP = {
    *"a an and any are as at be can could did do does document documents file files find for from get give "
    "has have her here his how i in is it its me my of on or our please pdf resume cv show tell that the their "
    "them there these this those to uploaded up us use was we what where which who whose with you your all every "
    "list mentioned referenced associated called named text page pages sheet docx xlsx excel word".split(),
    "link",
    "links",
    "url",
    "urls",
    "hyperlink",
    "hyperlinks",
    "website",
    "websites",
    "site",
    "sites",
    "web",
    "webpage",
    "homepage",
    "profile",
    "profiles",
    "address",
    "online",
}
MAX_LINKS = 50


@dataclass(frozen=True, slots=True)
class LinkQuery:
    """What a question asks for. Nothing specific (or ``list_all``) means every link."""

    domains: tuple[str, ...] = ()  # "github.com": named platforms and domains
    anchors: tuple[str, ...] = ()  # quoted phrases: 'the link behind "GitHub Profile"'
    terms: tuple[str, ...] = ()  # other meaningful words: "portfolio"
    list_all: bool = False
    explicit: bool = True  # asked with a word like "link"/"URL" (not only "profile"/"site")


@dataclass(frozen=True, slots=True)
class LinkHit:
    link: DocumentLink
    document_title: str
    file_type: str

    @property
    def location(self) -> str:
        link = self.link
        parts: list[str] = []
        if link.page_number is not None:
            parts.append(f"page {link.page_number}")
        if link.sheet_name:
            parts.append(f"sheet {link.sheet_name}" + (f", cell {link.cell_reference}" if link.cell_reference else ""))
        if link.section_name:
            parts.append(f"section {link.section_name}")
        return ", ".join(parts)


def detect_link_query(text: str) -> LinkQuery | None:
    """A ``LinkQuery`` when the question is about links/URLs or names a platform; otherwise None."""
    lowered = text.lower()
    domains: list[str] = []
    for name, platform_domains in PLATFORMS.items():
        if re.search(rf"\b{re.escape(name)}\b", lowered):
            domains.extend(d for d in platform_domains if d not in domains)
    for found in _DOMAIN_IN_TEXT.findall(lowered):
        domain = found.removeprefix("www.")
        if domain not in domains:
            domains.append(domain)
    explicit = bool(_STRONG_LINK_WORDS.search(text))
    if not domains and not explicit and not _WEAK_LINK_WORDS.search(text):
        return None

    platform_words = {w for name in PLATFORMS for w in name.split()}
    anchors = tuple(dict.fromkeys(q.strip() for q in _QUOTED.findall(text) if q.strip()))
    terms: list[str] = []
    for word in _WORD.findall(_QUOTED.sub(" ", lowered)):
        if len(word) >= 3 and word not in _STOP and word not in platform_words and "." not in word:
            if word not in terms:
                terms.append(word)
    return LinkQuery(
        domains=tuple(domains),
        anchors=anchors[:4],
        terms=tuple(terms[:8]),
        list_all=bool(_LIST_ALL.search(text)),
        explicit=explicit,
    )


def _escape(term: str) -> str:
    """Escape LIKE wildcards (used with ``escape="\\"``)."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _like(term: str) -> str:
    return f"%{_escape(term)}%"


class LinkService:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self.sessionmaker = sessionmaker

    def _base(
        self, principal: Principal, filters: RetrievalFilters | None
    ) -> Select[tuple[DocumentLink, str, FileType]]:
        stmt = (
            select(DocumentLink, Document.title, Document.file_type)
            .join(
                Document,
                and_(
                    Document.id == DocumentLink.document_id, Document.active_index_version == DocumentLink.index_version
                ),
            )
            .where(visible_documents_clause(principal))
        )
        if filters is not None:
            if filters.document_ids:
                stmt = stmt.where(Document.id.in_(filters.document_ids))
            if filters.category_ids:
                stmt = stmt.where(Document.category_id.in_(filters.category_ids))
            if filters.file_types:
                stmt = stmt.where(Document.file_type.in_([FileType(t) for t in filters.file_types]))
        return stmt.order_by(Document.created_at.desc(), Document.id, DocumentLink.position)

    async def _run(self, stmt: Select[tuple[DocumentLink, str, FileType]]) -> list[LinkHit]:
        async with self.sessionmaker() as s:
            rows = (await s.execute(stmt)).all()
        return [LinkHit(link=row[0], document_title=row[1], file_type=row[2].value) for row in rows]

    async def lookup(
        self,
        principal: Principal,
        query: LinkQuery,
        *,
        filters: RetrievalFilters | None = None,
        limit: int = MAX_LINKS,
        fallback: bool = True,
    ) -> list[LinkHit]:
        """Links matching ``query`` in documents the principal may view (active index version only).

        Domains are exact (``github.com`` or a subdomain of it); terms match anchor text or URL. "Show
        me all links" gets every link. With ``fallback``, an explicit link question whose words match
        nothing (such as "the portfolio website" when no anchor says "portfolio") also gets every link,
        and the answer model picks the relevant ones; without it, such a question gets no links.
        """
        base = self._base(principal, filters)
        if query.domains or query.anchors:
            # A platform, domain or quoted anchor text was named: match exactly that, nothing else.
            exact = or_(
                *[DocumentLink.domain == d for d in query.domains],
                *[DocumentLink.domain.like(f"%{_escape('.' + d)}", escape="\\") for d in query.domains],
                *[DocumentLink.anchor_text.ilike(_like(a), escape="\\") for a in query.anchors],
            )
            return await self._run(base.where(exact).limit(limit))
        if query.terms and not query.list_all:
            term_match = or_(
                *[DocumentLink.anchor_text.ilike(_like(t), escape="\\") for t in query.terms],
                *[DocumentLink.url.ilike(_like(t), escape="\\") for t in query.terms],
                *[DocumentLink.section_name.ilike(_like(t), escape="\\") for t in query.terms],
            )
            hits = await self._run(base.where(term_match).limit(limit))
            if hits or not query.explicit:
                return hits
        if not query.list_all and not (fallback and query.explicit):
            return []  # "What is our risk profile?" is not a link question unless a link matches its words
        return await self._run(base.limit(limit))

    async def search(
        self, principal: Principal, text: str, *, filters: RetrievalFilters | None = None, limit: int = MAX_LINKS
    ) -> list[LinkHit]:
        return await self.lookup(principal, parse_link_search(text), filters=filters, limit=limit)

    async def for_document(self, principal: Principal, document_id: uuid.UUID) -> list[LinkHit]:
        """Every link of one document (the caller has already checked it may view it)."""
        return await self._run(self._base(principal, RetrievalFilters(document_ids=(document_id,))))


def link_sources(hits: Sequence[LinkHit]) -> list[RetrievedChunk]:
    """One citable source per document listing its matched links with exact targets and locations."""
    by_document: dict[uuid.UUID, list[LinkHit]] = defaultdict(list)
    for hit in hits:
        by_document[hit.link.document_id].append(hit)
    sources: list[RetrievedChunk] = []
    for document_id, document_hits in by_document.items():
        first = document_hits[0]
        lines = []
        for hit in document_hits:
            link = hit.link
            label = f"{link.anchor_text} -> {link.url}" if link.anchor_text else link.url
            kind = {"hyperlink": "hyperlink", "url": "written as text", "email": "e-mail link"}.get(link.link_type, "")
            where = ", ".join(p for p in (hit.location, kind) if p)
            lines.append(f"- {label}" + (f" ({where})" if where else ""))
        text = "Hyperlinks extracted from this document (exact link targets):\n" + "\n".join(lines)
        version = first.link.index_version
        sources.append(
            RetrievedChunk(
                chunk_id=uuid.uuid5(_NAMESPACE, f"{document_id}:{version}:links"),
                document_id=document_id,
                document_title=first.document_title,
                file_type=first.file_type,
                kind=LINK_SOURCE_KIND,
                text=text,
                token_count=len(text) // 4 + 1,
                index_version=version,
                relevance=1.0,
            )
        )
    return sources


def parse_link_search(text: str) -> LinkQuery:
    """A search box query: a link question, a platform/domain, or plain keywords matched against links."""
    detected = detect_link_query(text)
    if detected is not None:
        return detected
    words = tuple(dict.fromkeys(w for w in _WORD.findall(text.lower()) if len(w) >= 2 and w not in _STOP))
    return LinkQuery(terms=words[:8], explicit=not words)


def to_link_read(hit: LinkHit) -> LinkRead:
    link = hit.link
    return LinkRead(
        id=link.id,
        document_id=link.document_id,
        document_title=hit.document_title,
        file_type=hit.file_type,
        url=link.url,
        domain=link.domain,
        link_type=link.link_type,
        anchor_text=link.anchor_text,
        page_number=link.page_number,
        sheet_name=link.sheet_name,
        cell_reference=link.cell_reference,
        section_name=link.section_name,
        location=hit.location,
        created_at=link.created_at,
    )
