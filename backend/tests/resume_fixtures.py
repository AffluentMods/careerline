# SPDX-License-Identifier: Apache-2.0
"""Fictional resumes built at test time. No real people, employers, or contact details."""

from __future__ import annotations

import io
import zipfile

import docx

CLASSIC_TEXT = """Jordan Rivera
Security Engineer
Springfield, ST | jordan@example.com | (555) 010-0100 | linkedin.com/in/jordan-example
SUMMARY
Blue team generalist with five years in detection and response.
PROFESSIONAL EXPERIENCE
Northwind Tools — Springfield, ST
Security Analyst  Jan 2021 – Present
• Reduced alert noise by 30% by tuning SIEM correlation rules and retiring stale detections that
no longer matched the environment.
• Triaged phishing reports from employees.
Help Desk Technician | Contoso Ltd | 06/2018 - 12/2020
- Supported 300 users across three sites
- Imaged laptops
EDUCATION
Example State University, Springfield, ST
Bachelor of Science in Information Systems, May 2018
CERTIFICATIONS
• Example Security+ — Example Cert Body, 2021
• Example Cloud Practitioner (in progress), expected 2027
SKILLS
Languages: Python, PowerShell, Bash
Tools: Splunk, Wireshark; Nmap
"""


def make_docx() -> bytes:
    """A two-column style resume: contact details in the header, skills in a table."""
    d = docx.Document()
    d.sections[0].header.paragraphs[0].text = "Casey Lin | casey@example.com | 555-010-0199"
    d.add_heading("Casey Lin", 0)
    d.add_paragraph("IT Support Specialist")
    d.add_heading("Work Experience", 1)
    d.add_paragraph("IT Support Specialist at Fabrikam Services, 2019 - Present")
    d.add_paragraph("Resolved 40 tickets per week on average", style="List Bullet")
    d.add_paragraph("Maintained the asset inventory", style="List Bullet")
    d.add_paragraph("Desk Clerk, Example City Library, 2016 - 2019")
    d.add_paragraph("Helped patrons with the public computers", style="List Bullet")
    d.add_heading("Education", 1)
    d.add_paragraph("Example Community College")
    d.add_paragraph("Associate of Applied Science in Network Administration, 2019")
    d.add_heading("Skills", 1)
    table = d.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Windows, Active Directory"
    table.cell(0, 1).text = "Networking, Ticketing"
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _pdf_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(lines: list[str]) -> bytes:
    """A minimal single-page PDF that shows each line of text."""
    ops = " ".join(f"({_pdf_escape(line)}) '" for line in lines)
    content = f"BT /F1 11 Tf 14 TL 50 760 Td {ops} ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


PDF_LINES = [
    "Riley Park",
    "Network Engineer",
    "riley@example.com",
    "Experience",
    "Network Engineer | Tailspin Networks | 2020 - Present",
    "- Migrated 12 branch offices to SD-WAN",
    "- Ran the on-call rotation",
    "Skills",
    "BGP, OSPF, Python",
]


def make_zip(members: dict[str, bytes], compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()
