# SPDX-License-Identifier: Apache-2.0
"""Checking an exported file the way applicant tracking systems (ATS) read it.

An ATS doesn't look at the page: it pulls the text out of the file. This module does the
same (``pypdf`` for PDF, ``python-docx`` for Word) and checks what a parser would see:

* the name and every contact detail come through as literal text;
* nothing garbled (replacement characters, stray control characters, broken spacing);
* the section headings come out in reading order;
* which of the posting's keywords the text covers. A keyword the user's verified facts
  support but the document leaves out is "could add"; one the facts don't support is a
  real gap and is never suggested for stuffing.
"""

from __future__ import annotations

import io
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Any

import docx
from pypdf import PdfReader

from careerline.ai.scoring import extract_requirements, tokens

RESUME_SECTIONS = ("SUMMARY", "EXPERIENCE", "PROJECTS", "EDUCATION", "CERTIFICATIONS", "SKILLS")

# Words that show up in every posting and say nothing about the job's skills.
_FILLER_WORDS = """
ability able about across activities additional also apply applicants based benefits best
both build building candidate candidates career company competitive create culture customer
customers daily day degree demonstrated description desired drive duties employees employer
employment environment equal etc excellent experience experienced familiarity fast field
following full good great growing growth help highly hire hiring ideal include including
individual job join knowledge level looking make management manager may must new non offer
opportunity other paced part people plus position preferred previous proven provide
qualifications related required requirements responsibilities role salary similar skill
skills solutions strong successful support team teams time understanding using well within
work working world years year you your our will can who what when where while with without
"""
_FILLER = frozenset(_FILLER_WORDS.split())
_TECHY = re.compile(r"[+#./]|\d|[a-z][A-Z]")


@dataclass
class Extracted:
    text: str
    pages: int


def extract(data: bytes, fmt: str) -> Extracted:
    if fmt == "pdf":
        reader = PdfReader(io.BytesIO(data))
        return Extracted(
            "\n".join(page.extract_text() or "" for page in reader.pages), len(reader.pages)
        )
    document = docx.Document(io.BytesIO(data))
    return Extracted("\n".join(p.text for p in document.paragraphs), 0)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip().lower()


def _contains(haystack: str, needle: str) -> bool:
    n = _norm(needle)
    if not n:
        return True
    return re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", haystack) is not None


def posting_keywords(
    title: str, description: str, skill_names: list[str], limit: int = 25
) -> list[str]:
    """The skills and terms a screener would look for, in order of importance.

    The user's own skill names come first when the posting mentions them; then technical
    terms and words repeated across the posting's requirement lines."""
    lowered = _norm(f"{title} {description}")
    found: list[str] = []
    for name in skill_names:
        if name and _contains(lowered, name):
            found.append(name)
    requirements = " ".join(extract_requirements(description, limit=40)) or description
    raw_words = re.findall(r"[A-Za-z][A-Za-z0-9+#./-]*[A-Za-z0-9+#]|[A-Za-z]", requirements)
    counts: Counter[str] = Counter()
    original: dict[str, str] = {}
    for word in raw_words:
        key = word.lower().strip(".")
        if key in _FILLER or (key not in tokens(key) and not _TECHY.search(word)):
            continue
        counts[key] += 1
        original.setdefault(key, word.strip("."))
    for key, n in counts.most_common():
        word = original[key]
        notable = n >= 2 or _TECHY.search(word) or (word.isupper() and len(word) >= 2)
        if notable and not any(_norm(f) == key for f in found):
            found.append(word)
        if len(found) >= limit:
            break
    return found[:limit]


def _garbled(text: str) -> list[str]:
    problems = []
    if "�" in text:
        problems.append("Some characters came out as � (unreadable).")
    controls = sum(1 for c in text if unicodedata.category(c) == "Cc" and c not in "\n\t\r")
    if controls:
        problems.append(f"{controls} invisible control character(s) in the text.")
    letters = sum(c.isalpha() for c in text)
    if text.strip() and letters / max(1, len(text.strip())) < 0.5:
        problems.append("Less than half of the text is letters; a parser may read it as junk.")
    if re.search(r"\b(?:[A-Za-z] ){6,}[A-Za-z]\b", text):
        problems.append("Some words came out letter-by-letter (s p a c e d), which hurts matching.")
    return problems


def check(
    *,
    data: bytes,
    fmt: str,
    kind: str,
    name: str,
    contact: list[str],
    target_title: str = "",
    target_description: str = "",
    skill_names: list[str] | None = None,
    vault_text: str = "",
) -> dict[str, Any]:
    extracted = extract(data, fmt)
    flat = _norm(extracted.text)
    checks: list[dict[str, Any]] = []

    readable = bool(flat)
    checks.append(
        {
            "id": "text",
            "ok": readable,
            "label": "Text can be read",
            "detail": ""
            if readable
            else "No text came out of the file; an ATS would see a blank page.",
        }
    )

    missing_contact = [c for c in [name, *contact] if c and not _contains(flat, c)]
    checks.append(
        {
            "id": "contact",
            "ok": not missing_contact,
            "label": "Name and contact details come through",
            "detail": "Not found as text: " + ", ".join(missing_contact) if missing_contact else "",
        }
    )
    if not contact:
        checks.append(
            {
                "id": "contact_present",
                "ok": False,
                "label": "Contact details on the page",
                "detail": "Add an email or phone number in Career Vault → Profile; recruiters "
                "need a way to reach you.",
            }
        )

    problems = _garbled(extracted.text)
    checks.append(
        {
            "id": "garbled",
            "ok": not problems,
            "label": "No garbled characters",
            "detail": " ".join(problems),
        }
    )

    if kind == "resume":
        positions = [(h, extracted.text.find(h)) for h in RESUME_SECTIONS]
        present = [(h, p) for h, p in positions if p >= 0]
        ordered = [p for _, p in present] == sorted(p for _, p in present)
        checks.append(
            {
                "id": "order",
                "ok": ordered and bool(present),
                "label": "Sections read in order",
                "detail": ""
                if ordered and present
                else "Section headings came out of order or are missing: "
                + ", ".join(h for h, _ in present),
            }
        )
        if fmt == "pdf" and extracted.pages > 2:
            checks.append(
                {
                    "id": "length",
                    "ok": False,
                    "label": "Two pages or fewer",
                    "detail": f"It's {extracted.pages} pages. Most recruiters read one or two; "
                    "remove the least relevant bullets.",
                }
            )

    keywords: dict[str, Any] | None = None
    if target_description.strip():
        wanted = posting_keywords(target_title, target_description, skill_names or [])
        vault = _norm(vault_text)
        covered = [k for k in wanted if _contains(flat, k)]
        could_add = [k for k in wanted if k not in covered and _contains(vault, k)]
        gaps = [k for k in wanted if k not in covered and k not in could_add]
        keywords = {
            "total": len(wanted),
            "covered": covered,
            "could_add": could_add,
            "gaps": gaps,
            "score": round(100 * len(covered) / len(wanted)) if wanted else None,
        }

    return {
        "format": fmt,
        "pages": extracted.pages,
        "ok": all(c["ok"] for c in checks),
        "checks": checks,
        "keywords": keywords,
        "text": extracted.text[:20_000],
    }
