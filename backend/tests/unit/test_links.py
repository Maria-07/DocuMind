"""Hyperlink extraction (PDF, DOCX, XLSX, XLS), URL helpers, and link-question detection."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.rag.answering import split_passages, unverified_urls
from app.rag.chunking.chunker import Chunker
from app.rag.ingestion.links import clean_target, dedupe, domain_of, make_link, text_links, urls_in_text
from app.rag.ingestion.parsers.docx import parse_docx
from app.rag.ingestion.parsers.excel import parse_xls, parse_xlsx, xls_cell_links
from app.rag.ingestion.parsers.pdf import parse_pdf
from app.rag.retrieval import RetrievedChunk
from app.services.links import detect_link_query, parse_link_search
from tests.documents import (
    GITHUB,
    LINKEDIN,
    PORTFOLIO,
    REPO,
    build_docx,
    build_docx_with_links,
    build_pdf,
    build_pdf_with_links,
    build_xls_with_url,
    build_xlsx_with_links,
    write,
)


def _by_url(links: list) -> dict[str, list]:  # type: ignore[type-arg]
    out: dict[str, list] = {}  # type: ignore[type-arg]
    for link in links:
        out.setdefault(link.url, []).append(link)
    return out


# --- URL helpers ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("see https://example.org/docs. Thanks", ["https://example.org/docs"]),
        (
            "(https://example.org/a_(b)) and https://x.io/q?a=1&b=2,",
            ["https://example.org/a_(b)", "https://x.io/q?a=1&b=2"],
        ),
        ("visit www.jane.dev/blog!", ["www.jane.dev/blog"]),
        ('<a href="https://evil.example">x</a>', ["https://evil.example"]),
        ("no links here, just example.org", []),
    ],
)
def test_urls_in_text_trims_sentence_punctuation_but_keeps_the_url(text: str, expected: list[str]) -> None:
    assert urls_in_text(text) == expected


def test_only_web_and_mail_targets_are_kept() -> None:
    assert clean_target("https://github.com/x") == "https://github.com/x"
    assert clean_target("  www.jane.dev  ") == "https://www.jane.dev"
    assert clean_target("mailto:jane@example.org") == "mailto:jane@example.org"
    for bad in ("javascript:alert(1)", "file:///etc/passwd", "data:text/html,hi", "https://", "#section", "", None):
        assert clean_target(bad) is None
    assert clean_target("https://x.org/" + "a" * 3000) is None


def test_domain_and_link_type() -> None:
    assert domain_of("https://www.GitHub.com/x") == "github.com"
    assert domain_of("mailto:jane@Example.org") == "example.org"
    hidden = make_link(GITHUB, anchor="GitHub Profile", embedded=True)
    assert hidden is not None and hidden.link_type == "hyperlink" and hidden.anchor_text == "GitHub Profile"
    # An anchor that is just the URL is not anchor text.
    shown = make_link(GITHUB, anchor="github.com/jane-doe", embedded=True)
    assert shown is not None and shown.link_type == "url" and shown.anchor_text is None
    mail = make_link("mailto:jane@example.org", anchor="Email me", embedded=True)
    assert mail is not None and mail.link_type == "email"


def test_dedupe_keeps_one_link_per_location_preferring_the_anchored_one() -> None:
    a = make_link(GITHUB, embedded=False, page=1)
    b = make_link(GITHUB, anchor="GitHub Profile", embedded=True, page=1)
    c = make_link(GITHUB, embedded=False, page=2)
    result = dedupe([x for x in (a, b, c) if x])
    assert [(x.page, x.link_type) for x in result] == [(1, "hyperlink"), (2, "url")]


# --- PDF --------------------------------------------------------------------------------------------


def test_pdf_hidden_targets_raw_urls_and_pages(tmp_path: Path) -> None:
    parsed = parse_pdf(write(tmp_path, "cv.pdf", build_pdf_with_links()), max_pages=10)
    links = _by_url(parsed.links)

    github = links[GITHUB][0]
    assert (github.anchor_text, github.link_type, github.page) == ("GitHub Profile", "hyperlink", 1)
    assert links[LINKEDIN][0].anchor_text == "LinkedIn"
    raw = links["https://talks.example.org/jane"][0]
    assert raw.link_type == "url" and raw.anchor_text is None and raw.page == 1
    assert (links[REPO][0].page, links[PORTFOLIO][0].page) == (2, 2)
    assert links[PORTFOLIO][0].anchor_text == "Personal Portfolio"

    # Hidden targets become part of the page text (searchable, quotable); visible URLs are not repeated.
    page1 = next(b.text for b in parsed.blocks if b.page == 1)
    assert "Links on this page:" in page1 and f"- GitHub Profile: {GITHUB}" in page1
    assert page1.count("https://talks.example.org/jane") == 1


def test_pdf_without_links_is_unchanged(tmp_path: Path) -> None:
    parsed = parse_pdf(write(tmp_path, "plain.pdf", build_pdf(["Plain text only."])), max_pages=10)
    assert parsed.links == []
    assert "Links on this page" not in parsed.blocks[0].text


# --- DOCX -------------------------------------------------------------------------------------------


def test_docx_hyperlinks_field_codes_tables_and_raw_urls(tmp_path: Path) -> None:
    parsed = parse_docx(write(tmp_path, "cv.docx", build_docx_with_links()))
    links = _by_url(parsed.links)

    github = links[GITHUB][0]
    assert (github.anchor_text, github.link_type, github.section_path) == (
        "GitHub Profile",
        "hyperlink",
        "Jane Doe > Contact",
    )
    assert links[LINKEDIN][0].anchor_text == "LinkedIn"
    assert links["https://www.janedoe.dev/blog"][0].link_type == "url"
    assert links[PORTFOLIO][0].section_path == "Jane Doe > Portfolio"  # HYPERLINK field code
    assert links[REPO][0].anchor_text == "Project Repository"  # inside a table cell

    text = "\n".join(b.text for b in parsed.blocks)
    assert f"GitHub Profile ({GITHUB})" in text and f"LinkedIn ({LINKEDIN})" in text
    assert f"Project Repository ({REPO})" in text
    # Section titles stay the visible heading text.
    assert all("http" not in (b.section_path or "") for b in parsed.blocks)


def test_docx_without_links_reads_exactly_as_before(tmp_path: Path) -> None:
    parsed = parse_docx(write(tmp_path, "policy.docx", build_docx()))
    assert parsed.links == []
    assert any("Passwords must be at least 14 characters" in b.text for b in parsed.blocks)


# --- Excel ------------------------------------------------------------------------------------------


def test_xlsx_cell_links_formulas_and_multiple_sheets(tmp_path: Path) -> None:
    parsed = parse_xlsx(write(tmp_path, "people.xlsx", build_xlsx_with_links()), max_rows=100)
    links = _by_url(parsed.links)

    github = links[GITHUB][0]
    assert (github.sheet, github.cell, github.anchor_text, github.link_type) == ("People", "B2", "GitHub", "hyperlink")
    linkedin = links[LINKEDIN][0]  # =HYPERLINK(...) formula; no cached value, so the label is the display
    assert (linkedin.sheet, linkedin.cell, linkedin.anchor_text) == ("People", "C2", "LinkedIn")
    assert (links[REPO][0].sheet, links[REPO][0].cell) == ("Sites", "A3")
    written = links[PORTFOLIO + "/about"][0]
    assert (written.sheet, written.cell, written.link_type) == ("Sites", "A2", "url")

    rows = {b.sheet: b.rows for b in parsed.blocks}
    assert f"Profile: GitHub ({GITHUB})" in rows["People"][0][1]
    assert f"Site: Project Repository ({REPO})" in rows["Sites"][1][1]


def test_xls_written_urls_and_hyperlink_records(tmp_path: Path) -> None:
    parsed = parse_xls(write(tmp_path, "contacts.xls", build_xls_with_url()), max_rows=100)
    assert [(x.url, x.sheet, x.cell) for x in parsed.links] == [(PORTFOLIO, "Contacts", "B2")]
    # xlwt cannot write HLINK records; check the mapping of xlrd's hyperlink objects directly.
    sheet = SimpleNamespace(
        hyperlink_map={
            (1, 1): SimpleNamespace(type="url", url_or_path=GITHUB + "\x00", desc="GitHub\x00"),
            (2, 1): SimpleNamespace(type="local file", url_or_path="C:\\secret.txt", desc=None),
        }
    )
    assert xls_cell_links(sheet) == {(2, 1): (GITHUB, "GitHub")}


# --- chunking and answering -------------------------------------------------------------------------


def test_every_extracted_url_ends_up_in_some_chunk(tmp_path: Path) -> None:
    from tests.fakes import WordCounter

    chunker = Chunker(WordCounter(), chunk_size=60, chunk_overlap=10)
    for name, data, parse in (
        ("cv.pdf", build_pdf_with_links(), lambda p: parse_pdf(p, max_pages=10)),
        ("cv.docx", build_docx_with_links(), parse_docx),
        ("people.xlsx", build_xlsx_with_links(), lambda p: parse_xlsx(p, max_rows=100)),
    ):
        parsed = parse(write(tmp_path, name, data))
        chunks = chunker.chunk(parsed)
        for link in parsed.links:
            if link.cell and link.cell[1:] == "1":
                continue  # header-row links are recorded, not inlined
            assert any(link.url.removeprefix("https://") in c.text for c in chunks), (name, link.url)


def test_link_lists_are_cited_line_by_line_and_unverified_urls_are_found() -> None:
    text = f"Hyperlinks extracted from this document (exact link targets):\n- GitHub Profile -> {GITHUB} (page 1)"
    assert split_passages(text, "links") == text.split("\n")
    chunk = RetrievedChunk(
        chunk_id=__import__("uuid").uuid4(),
        document_id=__import__("uuid").uuid4(),
        document_title="CV",
        file_type="pdf",
        kind="links",
        text=text,
        token_count=10,
        index_version=1,
    )
    assert unverified_urls(f"Here it is: {GITHUB}.", [chunk]) == []
    assert unverified_urls("Try https://github.com/jane-d0e instead.", [chunk]) == ["https://github.com/jane-d0e"]


# --- question detection -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "domains", "anchors", "terms", "list_all"),
    [
        ("What is the GitHub URL in this document?", ("github.com", "github.io"), (), (), False),
        ("Find the LinkedIn profile.", ("linkedin.com",), (), (), False),
        ("What is the portfolio link?", (), (), ("portfolio",), False),
        ("Show me all links in this document.", (), (), (), True),
        ("List all URLs in this document.", (), (), (), True),
        (
            'Find the link associated with the text "GitHub Profile".',
            ("github.com", "github.io"),
            ("GitHub Profile",),
            (),
            False,
        ),
        ("Which websites are referenced in my uploaded documents?", (), (), (), True),
    ],
)
def test_link_questions_are_recognized(
    question: str, domains: tuple[str, ...], anchors: tuple[str, ...], terms: tuple[str, ...], list_all: bool
) -> None:
    q = detect_link_query(question)
    assert q is not None
    assert (q.domains, q.anchors, q.terms, q.list_all) == (domains, anchors, terms, list_all)


def test_other_questions_are_not_link_questions() -> None:
    assert detect_link_query("How often must passwords be rotated?") is None
    weak = detect_link_query("What is our risk profile?")
    assert weak is not None and not weak.explicit  # only counts if a link matches "risk"
    assert parse_link_search("jane").terms == ("jane",)
    assert parse_link_search("github").domains == ("github.com", "github.io")


def test_text_links_carry_their_location() -> None:
    (link,) = text_links("see https://x.org", sheet="S", cell="A2")
    assert (link.sheet, link.cell, link.page) == ("S", "A2", None)
