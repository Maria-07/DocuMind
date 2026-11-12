"""Builders for realistic test documents (real files produced by real writer libraries)."""

from __future__ import annotations

import io
from datetime import date, datetime
from pathlib import Path
from typing import Any

import docx
import openpyxl
import xlwt
from fpdf import FPDF

LOREM = (
    "Retrieval augmented generation grounds answers in source documents. "
    "Every answer must cite the page or section it came from. "
)


def build_pdf(
    pages: list[str] | None = None,
    *,
    table: list[list[str]] | None = None,
    title: str = "Quarterly Report",
    author: str = "Finance Team",
    blank_pages: int = 0,
    password: str | None = None,
) -> bytes:
    pdf = FPDF()
    pdf.set_title(title)
    pdf.set_author(author)
    pdf.set_creation_date(datetime(2026, 3, 1, 9, 30))
    if password:
        pdf.set_encryption(owner_password=password + "-owner", user_password=password)
    pdf.set_font("Helvetica", size=11)
    for i, text in enumerate(pages or ["Page one text. " + LOREM]):
        pdf.add_page()
        pdf.multi_cell(0, 6, text)
        if table and i == 0:
            pdf.ln(4)
            with pdf.table() as t:
                for row in table:
                    r = t.row()
                    for cell in row:
                        r.cell(cell)
    for _ in range(blank_pages):
        pdf.add_page()
    return bytes(pdf.output())


def build_docx(*, with_table: bool = True) -> bytes:
    d = docx.Document()
    d.core_properties.title = "Security Policy"
    d.core_properties.author = "CISO Office"
    d.add_heading("Security Policy", level=0)
    d.add_paragraph("This policy applies to all employees and contractors.")
    d.add_heading("Access Control", level=1)
    d.add_heading("Passwords", level=2)
    d.add_paragraph("Passwords must be at least 14 characters and rotated every 90 days.")
    d.add_paragraph("Multi-factor authentication is mandatory for remote access.")
    d.add_heading("Data Retention", level=1)
    d.add_paragraph("Customer records are retained for seven years and then securely destroyed.")
    if with_table:
        table = d.add_table(rows=3, cols=2)
        for r, (k, v) in enumerate([("Data class", "Retention"), ("Invoices", "10 years"), ("Logs", "1 year")]):
            table.cell(r, 0).text, table.cell(r, 1).text = k, v
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def build_xlsx(rows: int = 5) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Revenue"
    ws.append(["Region", "Quarter", "Revenue", "Booked"])
    regions = ["EMEA", "APAC", "AMER"]
    for i in range(rows):
        ws.append([regions[i % 3], f"Q{i % 4 + 1}", 1000 * (i + 1) + 0.5, date(2026, 1, 1 + i % 28)])
    notes = wb.create_sheet("Notes")
    notes.append(["Note"])
    notes.append(["Figures are unaudited."])
    hidden = wb.create_sheet("Lookup")
    hidden.append(["Code", "Meaning"])
    hidden.append(["X1", "Internal"])
    hidden.sheet_state = "hidden"
    wb.properties.title = "Revenue 2026"
    wb.properties.creator = "Finance Team"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_xls() -> bytes:
    wb = xlwt.Workbook()
    ws = wb.add_sheet("Budget")
    date_style = xlwt.easyxf(num_format_str="YYYY-MM-DD")
    for c, h in enumerate(["Department", "Budget", "Approved", "Approved on"]):
        ws.write(0, c, h)
    for r, (dept, budget, ok) in enumerate([("Engineering", 120000, True), ("Sales", 80000.5, False)], start=1):
        ws.write(r, 0, dept)
        ws.write(r, 1, budget)
        ws.write(r, 2, ok)
        ws.write(r, 3, datetime(2026, 2, r), date_style)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def write(tmp: Path, name: str, data: bytes) -> Path:
    path = tmp / name
    path.write_bytes(data)
    return path


# --- documents with hyperlinks -------------------------------------------------------------------

GITHUB = "https://github.com/jane-doe"
LINKEDIN = "https://www.linkedin.com/in/jane-doe"
PORTFOLIO = "https://janedoe.dev"
REPO = "https://github.com/jane-doe/docu-search"


def build_pdf_with_links() -> bytes:
    """A two-page resume: links hidden behind anchor text (annotations) plus a URL written as text."""
    pdf = FPDF()
    pdf.set_title("Jane Doe Resume")
    pdf.set_font("Helvetica", size=11)
    pdf.add_page()
    pdf.multi_cell(0, 6, "Jane Doe. Senior software engineer building search and data platforms.")
    pdf.ln(2)
    pdf.write(6, "Code: ")
    pdf.write(6, "GitHub Profile", link=GITHUB)
    pdf.write(6, "  Network: ")
    pdf.write(6, "LinkedIn", link=LINKEDIN)
    pdf.ln(8)
    pdf.multi_cell(0, 6, "Docs for my talks are at https://talks.example.org/jane. Thanks for reading.")
    pdf.add_page()
    pdf.multi_cell(0, 6, "Projects. Built a document search engine used by three teams.")
    pdf.ln(2)
    pdf.write(6, "Project Repository", link=REPO)
    pdf.ln(8)
    pdf.write(6, "Personal Portfolio", link=PORTFOLIO)
    return bytes(pdf.output())


def _add_hyperlink(paragraph: Any, url: str, text: str) -> None:
    from docx.opc.constants import RELATIONSHIP_TYPE
    from docx.oxml.shared import OxmlElement, qn

    rid = paragraph.part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), rid)
    run = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = text
    run.append(t)
    link.append(run)
    paragraph._p.append(link)


def _add_field_hyperlink(paragraph: Any, url: str, text: str) -> None:
    """A HYPERLINK field code (how some tools write links) instead of a w:hyperlink element."""
    from docx.oxml.shared import OxmlElement, qn

    def run_with(child: Any) -> None:
        r = OxmlElement("w:r")
        r.append(child)
        paragraph._p.append(r)

    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    run_with(begin)
    instr = OxmlElement("w:instrText")
    instr.text = f' HYPERLINK "{url}" '
    run_with(instr)
    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    run_with(sep)
    t = OxmlElement("w:t")
    t.text = text
    run_with(t)
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run_with(end)


def build_docx_with_links() -> bytes:
    d = docx.Document()
    d.core_properties.title = "Jane Doe CV"
    d.add_heading("Jane Doe", level=0)
    d.add_heading("Contact", level=1)
    p = d.add_paragraph("Find my code on ")
    _add_hyperlink(p, GITHUB, "GitHub Profile")
    p.add_run(" and my network on ")
    _add_hyperlink(p, LINKEDIN, "LinkedIn")
    p.add_run(".")
    d.add_paragraph("Blog: www.janedoe.dev/blog for long reads.")
    d.add_heading("Portfolio", level=1)
    _add_field_hyperlink(d.add_paragraph(), PORTFOLIO, "Personal Portfolio")
    table = d.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Project", "Link"
    table.cell(1, 0).text = "Docu Search"
    _add_hyperlink(table.cell(1, 1).paragraphs[0], REPO, "Project Repository")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def build_xlsx_with_links() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "People"
    ws.append(["Name", "Profile", "Network"])
    ws.append(["Jane Doe", "GitHub", None])
    ws["B2"].hyperlink = GITHUB
    ws["C2"] = f'=HYPERLINK("{LINKEDIN}","LinkedIn")'
    sites = wb.create_sheet("Sites")
    sites.append(["Site", "Note"])
    sites.append([PORTFOLIO + "/about", "written out"])
    sites.append(["Project Repository", "linked"])
    sites["A3"].hyperlink = REPO
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_xls_with_url() -> bytes:
    wb = xlwt.Workbook()
    ws = wb.add_sheet("Contacts")
    ws.write(0, 0, "Name")
    ws.write(0, 1, "Website")
    ws.write(1, 0, "Jane Doe")
    ws.write(1, 1, PORTFOLIO)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
