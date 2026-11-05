# SPDX-License-Identifier: Apache-2.0
"""Text extraction worker. Runs in its own process; never imported by the app.

    python -I -m waypoint.vault.importer.worker pdf|docx  < file  > {"text": "..."}

Exit codes: 0 ok, 2 the file could not be read, 3 bad usage. Only text is
extracted: no macros, embedded objects, images, or links are followed.
"""

from __future__ import annotations

import io
import json
import re
import sys

MAX_PAGES = 10
MAX_CHARS = 100_000
MAX_MEMORY = 512 * 1024 * 1024

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _limit_memory() -> None:
    if sys.platform != "win32":  # no RLIMIT on Windows; the timeout still applies
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (MAX_MEMORY, MAX_MEMORY))


def _docx_lines(data: bytes) -> list[str]:
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(io.BytesIO(data))
    lines: list[str] = []

    def add_paragraph(p: Paragraph) -> None:
        text = p.text
        if not text.strip():
            return
        ppr = p._p.pPr
        style = ""
        try:
            style = (p.style.name or "") if p.style is not None else ""
        except (KeyError, ValueError):
            style = ""
        is_list = (ppr is not None and ppr.numPr is not None) or style.startswith("List")
        for part in text.splitlines():
            if part.strip():
                lines.append(f"• {part}" if is_list else part)

    def add_table(t: Table) -> None:
        seen: set[int] = set()
        for row in t.rows:
            for cell in row.cells:
                if id(cell._tc) in seen:  # merged cells repeat
                    continue
                seen.add(id(cell._tc))
                for p in cell.paragraphs:
                    add_paragraph(p)

    for section in doc.sections:
        for p in section.header.paragraphs:
            add_paragraph(p)
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            add_paragraph(Paragraph(child, doc))
        elif child.tag == qn("w:tbl"):
            add_table(Table(child, doc))
        if sum(len(x) for x in lines) > MAX_CHARS:
            break
    return lines


def _pdf_lines(data: bytes) -> list[str]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data), strict=False)
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("encrypted")
    lines: list[str] = []
    for page in reader.pages[:MAX_PAGES]:
        lines.extend((page.extract_text() or "").splitlines())
        if sum(len(x) for x in lines) > MAX_CHARS:
            break
    return lines


def clean(lines: list[str]) -> str:
    out: list[str] = []
    for line in lines:
        line = _CONTROL.sub("", line.replace("\t", " ").replace("\u00a0", " "))
        line = re.sub(r" {2,}", "  ", line).strip()
        out.append(line)
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:MAX_CHARS]


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in {"pdf", "docx"}:
        return 3
    _limit_memory()
    data = sys.stdin.buffer.read()
    try:
        lines = _pdf_lines(data) if argv[1] == "pdf" else _docx_lines(data)
    except Exception as exc:  # any parser failure means "unreadable"
        print(type(exc).__name__, file=sys.stderr)
        return 2
    sys.stdout.write(json.dumps({"text": clean(lines)}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
