# SPDX-License-Identifier: Apache-2.0
"""DOCX and PDF export with simple, ATS-friendly layouts.

Single column, standard fonts, real text (no images, tables, or text boxes), plain
section headings, and bullets. All text is escaped before it reaches reportlab, whose
Paragraph parses markup.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from xml.sax.saxutils import escape

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_MIME = "application/pdf"


@dataclass
class Header:
    name: str
    headline: str = ""
    contact: list[str] = field(default_factory=list)


def filename(header: Header, kind: str, company: str, ext: str) -> str:
    label = "Resume" if kind == "resume" else "Cover Letter"
    parts = [header.name or "Resume", company, label]
    raw = " - ".join(p for p in parts if p)
    return re.sub(r"[^\w .()-]+", "_", raw).strip(" ._")[:120] + f".{ext}"


# --- DOCX ------------------------------------------------------------------------------------


def _docx_base() -> Any:
    d = docx.Document()
    for section in d.sections:
        section.left_margin = section.right_margin = Inches(0.8)
        section.top_margin = section.bottom_margin = Inches(0.7)
    normal = d.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)
    return d


def _docx_header(d: Any, h: Header) -> None:
    p = d.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(h.name)
    run.bold = True
    run.font.size = Pt(18)
    if h.headline:
        sub = d.add_paragraph(h.headline)
        sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if h.contact:
        c = d.add_paragraph(" | ".join(h.contact))
        c.alignment = WD_ALIGN_PARAGRAPH.CENTER


def _docx_heading(d: Any, text: str) -> None:
    p = d.add_paragraph()
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.space_after = Pt(2)
    run = p.add_run(text.upper())
    run.bold = True


def resume_docx(content: dict[str, Any], h: Header) -> bytes:
    d = _docx_base()
    _docx_header(d, h)
    if content.get("summary"):
        _docx_heading(d, "Summary")
        d.add_paragraph(content["summary"]["text"])
    if content.get("experience"):
        _docx_heading(d, "Experience")
        for e in content["experience"]:
            p = d.add_paragraph()
            p.paragraph_format.space_before = Pt(6)
            p.add_run(e["title"]).bold = True
            if e.get("employer"):
                p.add_run(f", {e['employer']}")
            meta = " | ".join(x for x in (e.get("location", ""), e.get("dates", "")) if x)
            if meta:
                d.add_paragraph(meta)
            for b in e["bullets"]:
                d.add_paragraph(b["text"], style="List Bullet")
    if content.get("projects"):
        _docx_heading(d, "Projects")
        for pr in content["projects"]:
            p = d.add_paragraph()
            p.add_run(pr["name"]).bold = True
            extra = " | ".join(
                x for x in (pr.get("role", ""), pr.get("dates", ""), pr.get("url", "")) if x
            )
            if extra:
                p.add_run(f" | {extra}")
            for b in pr["bullets"]:
                d.add_paragraph(b["text"], style="List Bullet")
    for key, label in (("education", "Education"), ("certifications", "Certifications")):
        if content.get(key):
            _docx_heading(d, label)
            for e in content[key]:
                d.add_paragraph(e["text"])
    if content.get("skills"):
        _docx_heading(d, "Skills")
        for g in content["skills"]:
            p = d.add_paragraph()
            p.add_run(f"{g['category']}: ").bold = True
            p.add_run(", ".join(g["names"]))
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def letter_docx(content: dict[str, Any], h: Header, today: date) -> bytes:
    d = _docx_base()
    _docx_header(d, h)
    d.add_paragraph(f"{today:%B} {today.day}, {today.year}")
    d.add_paragraph(
        f"Dear {content['company']} team," if content.get("company") else "Dear Hiring Team,"
    )
    for para in content.get("paragraphs", []):
        d.add_paragraph(para["text"])
    d.add_paragraph("Sincerely,")
    d.add_paragraph(h.name)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


# --- PDF -------------------------------------------------------------------------------------

_BASE = ParagraphStyle("base", fontName="Helvetica", fontSize=10.5, leading=14)
_NAME = ParagraphStyle(
    "name", parent=_BASE, fontName="Helvetica-Bold", fontSize=18, leading=22, alignment=TA_CENTER
)
_CENTER = ParagraphStyle("center", parent=_BASE, alignment=TA_CENTER)
_HEAD = ParagraphStyle(
    "head", parent=_BASE, fontName="Helvetica-Bold", fontSize=11, spaceBefore=10, spaceAfter=3
)
_ROLE = ParagraphStyle("role", parent=_BASE, spaceBefore=6)
_META = ParagraphStyle("meta", parent=_BASE, textColor="#444444")
_PARA = ParagraphStyle("para", parent=_BASE, spaceAfter=8)


def _t(text: str) -> str:
    return escape(text or "")


def _pdf(flow: list[Any], title: str) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=LETTER,
        leftMargin=0.8 * inch,
        rightMargin=0.8 * inch,
        topMargin=0.7 * inch,
        bottomMargin=0.7 * inch,
        title=title,
        author="",
        subject="",
        creator="Waypoint",
    )
    doc.build(flow)
    return buf.getvalue()


def _pdf_header(h: Header) -> list[Any]:
    flow: list[Any] = [Paragraph(_t(h.name), _NAME)]
    if h.headline:
        flow.append(Paragraph(_t(h.headline), _CENTER))
    if h.contact:
        flow.append(Paragraph(_t(" | ".join(h.contact)), _CENTER))
    return flow


def _bullets(items: list[dict[str, Any]]) -> ListFlowable:
    return ListFlowable(
        [ListItem(Paragraph(_t(b["text"]), _BASE), leftIndent=12) for b in items],
        bulletType="bullet",
        start="•",
        leftIndent=12,
    )


def resume_pdf(content: dict[str, Any], h: Header) -> bytes:
    flow = _pdf_header(h)
    if content.get("summary"):
        flow += [Paragraph("SUMMARY", _HEAD), Paragraph(_t(content["summary"]["text"]), _BASE)]
    if content.get("experience"):
        flow.append(Paragraph("EXPERIENCE", _HEAD))
        for e in content["experience"]:
            employer = f", {_t(e['employer'])}" if e.get("employer") else ""
            flow.append(Paragraph(f"<b>{_t(e['title'])}</b>{employer}", _ROLE))
            meta = " | ".join(x for x in (e.get("location", ""), e.get("dates", "")) if x)
            if meta:
                flow.append(Paragraph(_t(meta), _META))
            if e["bullets"]:
                flow.append(_bullets(e["bullets"]))
    if content.get("projects"):
        flow.append(Paragraph("PROJECTS", _HEAD))
        for pr in content["projects"]:
            extra = " | ".join(
                x for x in (pr.get("role", ""), pr.get("dates", ""), pr.get("url", "")) if x
            )
            flow.append(
                Paragraph(f"<b>{_t(pr['name'])}</b>" + (f" | {_t(extra)}" if extra else ""), _ROLE)
            )
            if pr["bullets"]:
                flow.append(_bullets(pr["bullets"]))
    for key, label in (("education", "EDUCATION"), ("certifications", "CERTIFICATIONS")):
        if content.get(key):
            flow.append(Paragraph(label, _HEAD))
            flow += [Paragraph(_t(e["text"]), _BASE) for e in content[key]]
    if content.get("skills"):
        flow.append(Paragraph("SKILLS", _HEAD))
        flow += [
            Paragraph(f"<b>{_t(g['category'])}:</b> {_t(', '.join(g['names']))}", _BASE)
            for g in content["skills"]
        ]
    return _pdf(flow, f"{h.name} - Resume")


def letter_pdf(content: dict[str, Any], h: Header, today: date) -> bytes:
    flow = _pdf_header(h)
    flow.append(Spacer(1, 16))
    flow.append(Paragraph(_t(f"{today:%B} {today.day}, {today.year}"), _PARA))
    greeting = f"Dear {content['company']} team," if content.get("company") else "Dear Hiring Team,"
    flow.append(Paragraph(_t(greeting), _PARA))
    flow += [Paragraph(_t(p["text"]), _PARA) for p in content.get("paragraphs", [])]
    flow += [Paragraph("Sincerely,", _BASE), Paragraph(_t(h.name), _BASE)]
    return _pdf(flow, f"{h.name} - Cover Letter")
