"""Builders for realistic test documents (real files produced by real writer libraries)."""

from __future__ import annotations

import io
from datetime import date, datetime
from pathlib import Path

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
