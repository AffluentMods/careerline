# SPDX-License-Identifier: Apache-2.0
"""Turn resume text into draft vault entries with simple, predictable rules.

This is deliberately heuristic. It never invents facts: every value it emits is
a substring of the resume text, apart from the clearly marked placeholder
title "(add job title)" for a dated role whose title couldn't be found.
Everything it produces is saved as an unverified draft for the user to review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# --- vocabulary ----------------------------------------------------------------------

_HEADINGS: dict[str, str] = {}
for _section, _names in {
    "experience": [
        "experience",
        "work experience",
        "professional experience",
        "relevant experience",
        "employment",
        "employment history",
        "work history",
        "career history",
        "professional background",
        "experience highlights",
    ],
    "education": ["education", "education and training", "academic background", "academics"],
    "certifications": [
        "certifications",
        "certification",
        "certificates",
        "licenses and certifications",
        "licenses certifications",
        "certifications and licenses",
        "certifications and training",
        "professional certifications",
    ],
    "skills": [
        "skills",
        "technical skills",
        "core skills",
        "key skills",
        "core competencies",
        "competencies",
        "areas of expertise",
        "skills and tools",
        "tools and technologies",
        "technologies",
        "tools",
    ],
    "projects": ["projects", "personal projects", "selected projects", "key projects"],
    "summary": [
        "summary",
        "profile",
        "professional summary",
        "career summary",
        "about",
        "about me",
        "objective",
        "career objective",
    ],
    "other": [
        "references",
        "volunteer",
        "volunteering",
        "volunteer experience",
        "awards",
        "honors",
        "honors and awards",
        "interests",
        "hobbies",
        "publications",
        "languages",
        "activities",
        "affiliations",
    ],
}.items():
    for _n in _names:
        _HEADINGS[_n] = _section

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
)
_YEAR = r"(?<!\d)(?:19|20)\d{2}(?!\d)"
_POINT = rf"(?:{_MONTH}\s+{_YEAR}|(?<!\d)(?:0?[1-9]|1[0-2])\s*/\s*{_YEAR}|{_YEAR})"
_END = rf"(?:{_POINT}|present|current|now|today)"
_RANGE = re.compile(
    rf"\(?(?P<start>{_POINT})\s*(?:-|\u2013|\u2014|to|until|through)\s*(?P<end>{_END})\)?", re.I
)
_SINGLE = re.compile(rf"\(?(?P<exp>expected\s+|anticipated\s+)?(?P<point>{_POINT})\)?", re.I)

_BULLET = re.compile(r"^(?:[•●▪■◦‣∙·➢➤✓✔►\-\*\u2013\u2014]|o(?=\s))\s*")
_NUMBER = re.compile(r"\d|%|\$")
_EMAIL = re.compile(r"[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63})+")
_PHONE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
_URL = re.compile(
    r"(?:https?://)?(?:www\.)?(?:linkedin\.com|github\.com|gitlab\.com)/[^\s|,;]+"
    r"|https?://[^\s|,;]+",
    re.I,
)
_LOCATION = re.compile(r"\b[A-Z][A-Za-z.' -]{1,40},\s*[A-Z]{2}\b(?:\s+\d{5})?|\bRemote\b")
_TITLE_WORDS = re.compile(
    r"\b(analyst|engineer|manager|developer|specialist|administrator|admin|technician|"
    r"director|lead|consultant|intern|architect|officer|coordinator|assistant|associate|"
    r"designer|scientist|support|representative|supervisor|head|chief|president|owner|"
    r"founder|programmer|operator|tester|auditor|investigator|responder|instructor|"
    r"teacher|clerk|agent|advisor|adviser|accountant|nurse|cashier|server|driver|"
    r"mechanic|electrician|librarian|editor|writer|researcher|fellow)s?\b",
    re.I,
)
_COMPANY_WORDS = re.compile(
    r"\b(inc|llc|ltd|corp|corporation|company|co|group|bank|agency|department|dept|"
    r"services|technologies|technology|solutions|systems|labs|partners|university|"
    r"college|hospital|county|city of|state of|government|foundation|association|"
    r"consulting|holdings|industries|enterprises|gmbh|plc)\b\.?",
    re.I,
)
_INSTITUTION = re.compile(
    r"\b(university|college|institute|school|academy|polytechnic|conservatory|"
    r"community college|bootcamp)\b",
    re.I,
)
_DEGREE = re.compile(
    r"\b(bachelor|master|associate|doctor|ph\.?d|mba|b\.?s\.?c?|b\.?a\.?|m\.?s\.?c?|m\.?a\.?|"
    r"a\.?a\.?s?\.?|a\.?s\.?|diploma|certificate|ged|high school)\b",
    re.I,
)
_IN_PROGRESS = re.compile(
    r"\b(in progress|expected|pursuing|candidate|anticipated|ongoing)\b", re.I
)
_SPLIT = re.compile(r"\s+[|•·]\s+|\s+[\u2013\u2014-]\s+|\s{2,}|\s*\|\s*")
_TERMINAL = (".", "!", "?", ";", ":")
PLACEHOLDER_TITLE = "(add job title)"


@dataclass
class Draft:
    key: int
    kind: str
    data: dict[str, Any]
    parent: int | None = None


@dataclass
class ParsedResume:
    drafts: list[Draft] = field(default_factory=list)
    suggestions: dict[str, str] = field(default_factory=dict)
    sections: list[str] = field(default_factory=list)

    def add(self, kind: str, data: dict[str, Any], parent: int | None = None) -> Draft:
        draft = Draft(key=len(self.drafts), kind=kind, data=data, parent=parent)
        self.drafts.append(draft)
        return draft


# --- small helpers ------------------------------------------------------------------


def _clip(value: str, limit: int) -> str:
    value = re.sub(r"\s+", " ", value).strip(" ,;|-\u2013\u2014\t")
    return value[:limit].rstrip()


def _heading(line: str) -> str | None:
    if len(line) > 50:
        return None
    key = re.sub(r"[^a-z ]", " ", line.lower().replace("&", " and "))
    key = re.sub(r"\s+", " ", key).strip()
    return _HEADINGS.get(key)


def _strip_bullet(line: str) -> tuple[bool, str]:
    m = _BULLET.match(line)
    if m and len(line) > m.end():
        return True, line[m.end() :].strip()
    return False, line.strip()


def _partial(point: str) -> str | None:
    point = point.strip().lower()
    year = re.search(r"(19|20)\d{2}", point)
    if not year:
        return None
    if re.match(r"[a-z]", point):
        month = _MONTHS.get(point[:3])
        return f"{year.group(0)}-{month:02d}" if month else year.group(0)
    slash = re.match(r"(\d{1,2})\s*/", point)
    if slash:
        return f"{year.group(0)}-{int(slash.group(1)):02d}"
    return year.group(0)


def _dates(text: str) -> tuple[str | None, str | None, bool, str]:
    """Return (start, end, current, text without the dates)."""
    m = _RANGE.search(text)
    if m:
        start = _partial(m.group("start"))
        end_raw = m.group("end").lower()
        current = end_raw in {"present", "current", "now", "today"}
        end = None if current else _partial(end_raw)
        return start, end, current, (text[: m.start()] + " " + text[m.end() :]).strip()
    return None, None, False, text


def _single_date(text: str) -> tuple[str | None, bool, str]:
    """Return (date, marked expected, text without it)."""
    m = _SINGLE.search(text)
    if not m:
        return None, False, text
    rest = (text[: m.start()] + " " + text[m.end() :]).strip()
    return _partial(m.group("point")), bool(m.group("exp")), rest


def _parts(text: str) -> list[str]:
    parts = []
    for raw in _SPLIT.split(text):
        p = raw.strip(" ,;|-\u2013\u2014")
        if p:
            parts.extend(_split_at(p))
    return parts


def _split_at(text: str) -> list[str]:
    # "Security Analyst at Northwind Tools" -> two parts.
    m = re.match(r"(.+?)\s+(?:at|@)\s+(.+)", text)
    if m and _TITLE_WORDS.search(m.group(1)):
        return [m.group(1), m.group(2)]
    m = re.match(r"(.+?),\s+(.+)", text)
    if m and _TITLE_WORDS.search(m.group(1)) and not _LOCATION.fullmatch(text):
        return [m.group(1), m.group(2)]
    return [text]


def _pop_location(text: str) -> tuple[str, str]:
    found = list(_LOCATION.finditer(text))
    if not found:
        return text, ""
    m = found[-1]
    rest = (text[: m.start()] + text[m.end() :]).strip(" ,;|-\u2013\u2014")
    return rest, m.group(0)


# --- sections ------------------------------------------------------------------------


def _parse_header(lines: list[str], out: ParsedResume) -> None:
    joined = "\n".join(lines)
    if m := _EMAIL.search(joined):
        out.suggestions["email"] = m.group(0)
    if m := _PHONE.search(joined):
        out.suggestions["phone"] = m.group(0).strip()
    seen_urls: set[str] = set()
    for m in _URL.finditer(joined):
        url = m.group(0).rstrip(".)")
        if "@" in url:
            continue
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        url = re.sub(r"^http://", "https://", url, flags=re.I)
        if url.lower() in seen_urls or len(url) > 500:
            continue
        seen_urls.add(url.lower())
        host = re.sub(r"^https://(www\.)?", "", url, flags=re.I).split("/")[0].lower()
        label = {"linkedin.com": "LinkedIn", "github.com": "GitHub", "gitlab.com": "GitLab"}
        out.add("link", {"label": label.get(host, host)[:100], "url": url})

    plain: list[str] = []
    for line in lines:
        for seg in re.split(r"\s*[|•·]\s*|\s{2,}", line):
            seg = seg.strip()
            if not seg:
                continue
            if _EMAIL.search(seg) or _PHONE.search(seg) or _URL.search(seg):
                continue
            if loc := _LOCATION.fullmatch(seg):
                out.suggestions.setdefault("location_text", loc.group(0))
                continue
            plain.append(seg)
    for seg in plain:
        words = seg.split()
        if (
            "full_name" not in out.suggestions
            and 2 <= len(words) <= 5
            and len(seg) <= 60
            and not re.search(r"\d", seg)
        ):
            out.suggestions["full_name"] = _clip(seg, 200)
        elif (
            "full_name" in out.suggestions
            and "headline" not in out.suggestions
            and seg.casefold() != out.suggestions["full_name"].casefold()
        ):
            out.suggestions["headline"] = _clip(seg, 200)


def _is_continuation(prev: str, line: str) -> bool:
    if prev.endswith(_TERMINAL):
        return False
    if line[:1].islower() or prev.endswith((",", "-", "&", "and", "or", "of", "the", "to")):
        return True
    looks_like_header = bool(
        _TITLE_WORDS.search(line) or _COMPANY_WORDS.search(line) or _LOCATION.search(line)
    )
    return len(prev) >= 70 and not looks_like_header and len(line) > 25


def _bullet_kind(text: str) -> str:
    return "achievement" if _NUMBER.search(text) else "responsibility"


def _fill_role(role: Draft, candidates: list[str]) -> None:
    data = role.data
    rest: list[str] = []
    for raw in candidates:
        text, loc = _pop_location(raw)
        if loc and not data.get("location"):
            data["location"] = _clip(loc, 200)
        if text:
            rest.append(text)
    titled = [c for c in rest if _TITLE_WORDS.search(c) and not _COMPANY_WORDS.search(c)]
    title = data.get("title") or (titled[0] if titled else (rest[0] if rest else ""))
    if title and not data.get("title"):
        data["title"] = _clip(title, 200)
    for c in rest:
        if c == title:
            continue
        if not data.get("employer"):
            data["employer"] = _clip(c, 200)
        else:
            summary = (data.get("summary", "") + " " + c).strip()
            data["summary"] = _clip(summary, 4000)


def _parse_experience(lines: list[str], out: ParsedResume) -> None:
    role: Draft | None = None
    role_has_bullets = False
    pending: list[str] = []
    last: Draft | None = None

    for raw in lines:
        is_bullet, text = _strip_bullet(raw)
        if not text:
            continue
        start, end, _, rest = (None, None, False, text) if is_bullet else _dates(text)
        if start:
            role = out.add("role", {"title": "", "start": start, "end": end})
            _fill_role(role, pending + _parts(rest))
            if not role.data["title"]:
                # No usable title text: keep the dates, let the user fill in the rest.
                role.data["title"] = PLACEHOLDER_TITLE
            pending, last, role_has_bullets = [], None, False
            continue
        if is_bullet or (role is not None and len(text) > 90 and role_has_bullets):
            if last is not None and not is_bullet and _is_continuation(last.data["text"], text):
                last.data["text"] = _clip(last.data["text"] + " " + text, 1000)
                continue
            kind = _bullet_kind(text)
            if role is None and kind == "responsibility":
                kind = "achievement"  # responsibilities must belong to a role
            last = out.add(kind, {"text": _clip(text, 1000)}, role.key if role else None)
            role_has_bullets = True
            continue
        if last is not None and _is_continuation(last.data["text"], text):
            last.data["text"] = _clip(last.data["text"] + " " + text, 1000)
            if last.kind == "responsibility" and _NUMBER.search(text):
                last.kind = "achievement"
            continue
        if role is not None and not role_has_bullets:
            _fill_role(role, _parts(text))
            continue
        pending.append(text)
        last = None


def _parse_education(lines: list[str], out: ParsedResume) -> None:
    current: dict[str, Any] | None = None
    current_draft: Draft | None = None
    held: dict[str, Any] = {}  # credential seen before its institution
    this_year = datetime.now(UTC).year

    def flush() -> None:
        nonlocal current, current_draft
        if current and current.get("institution"):
            current_draft = out.add("education", current)
        current = None

    for raw in lines:
        is_bullet, text = _strip_bullet(raw)
        if not text:
            continue
        if is_bullet:
            if current is not None and current.get("institution"):
                flush()
            if current_draft is not None:
                out.add("achievement", {"text": _clip(text, 1000)}, current_draft.key)
            continue
        start, end, is_current, rest = _dates(text)
        expected = False
        if not start:
            end, expected, rest = _single_date(rest)
        for part in _parts(rest):
            part, _ = _pop_location(part)
            if not part:
                continue
            if _INSTITUTION.search(part):
                if current is not None and current.get("institution"):
                    flush()
                current = current or {**held}
                held = {}
                current["institution"] = _clip(part, 200)
            elif _DEGREE.search(part):
                target = current if current is not None else held
                if target.get("credential"):
                    continue
                cred, _, fld = part.partition(" in ")
                target["credential"] = _clip(cred, 200)
                if fld:
                    target["field"] = _clip(fld, 200)
        target = current if current is not None else held
        if start:
            target["start"] = start
        if end:
            target["end"] = end
        if is_current or expected or (end and int(end[:4]) > this_year):
            target["status"] = "in_progress"
            if is_current:
                target.pop("end", None)
    flush()


def _parse_certifications(lines: list[str], out: ParsedResume) -> None:
    for raw in lines:
        _, text = _strip_bullet(raw)
        if not text or len(text) > 250:
            continue
        in_progress = bool(_IN_PROGRESS.search(text))
        start, end, _, rest = _dates(text)
        when = end or start
        if not when:
            when, _, rest = _single_date(rest)
        rest = _IN_PROGRESS.sub("", rest)
        rest = re.sub(r"\(\s*\)", "", rest)
        parts = [
            p
            for p in re.split(r"\s+[|\u2013\u2014-]\s+|,\s+|\s+by\s+|\s*[()]\s*", rest)
            if p.strip()
        ]
        if not parts:
            continue
        data: dict[str, Any] = {
            "name": _clip(parts[0], 200),
            "status": "in_progress" if in_progress else "earned",
        }
        if len(parts) > 1 and len(parts[1]) <= 100:
            data["issuer"] = _clip(parts[1], 200)
        if when:
            data["target_date" if in_progress else "earned_on"] = when
        if data["name"]:
            out.add("certification", data)


def _parse_skills(lines: list[str], out: ParsedResume) -> None:
    seen: set[str] = set()
    for raw in lines:
        _, text = _strip_bullet(raw)
        category = ""
        label = re.match(r"([^:]{2,40}):\s*(.+)", text)
        if label:
            category, text = label.group(1).strip(), label.group(2)
        for item in re.split(r"\s*[,;|•·]\s*", text):
            item = item.strip(" .")
            if not item or len(item) > 100 or len(item.split()) > 6:
                continue
            key = item.casefold()
            if key in seen:
                continue
            seen.add(key)
            data = {"name": item}
            if category:
                data["category"] = _clip(category, 100)
            out.add("skill", data)


def _parse_projects(lines: list[str], out: ParsedResume) -> None:
    project: Draft | None = None
    last: Draft | None = None
    for raw in lines:
        is_bullet, text = _strip_bullet(raw)
        if not text:
            continue
        if is_bullet:
            if project is None:
                last = out.add("achievement", {"text": _clip(text, 1000)})
            else:
                last = out.add("achievement", {"text": _clip(text, 1000)}, project.key)
            continue
        if last is not None and _is_continuation(last.data["text"], text):
            last.data["text"] = _clip(last.data["text"] + " " + text, 1000)
            continue
        start, end, _, rest = _dates(text)
        url = ""
        if m := _URL.search(rest):
            url = m.group(0)
            url = url if url.lower().startswith("https://") else ""
            rest = (rest[: m.start()] + rest[m.end() :]).strip()
        parts = _parts(rest)
        if not parts:
            continue
        if project is not None and not start and not url and last is None:
            # A description line directly under the project name.
            desc = (project.data.get("description", "") + " " + text).strip()
            project.data["description"] = _clip(desc, 4000)
            continue
        data: dict[str, Any] = {"name": _clip(parts[0], 200)}
        if len(parts) > 1 and _TITLE_WORDS.search(parts[1]):
            data["your_role"] = _clip(parts[1], 200)
        if start:
            data["start"] = start
            data["end"] = end
        if url and len(url) <= 500:
            data["url"] = url
        project = out.add("project", data)
        last = None


# --- entry point ------------------------------------------------------------------------


def parse_resume(text: str) -> ParsedResume:
    out = ParsedResume()
    sections: dict[str, list[str]] = {"header": []}
    order: list[str] = []
    current = "header"
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        section = _heading(line)
        if section:
            current = section
            if section not in sections:
                sections[section] = []
                order.append(section)
            continue
        sections[current].append(line)

    out.sections = [s for s in order if s != "other"]
    header = sections["header"][:15]
    _parse_header(header, out)
    if "summary" in sections:
        summary = " ".join(_strip_bullet(x)[1] for x in sections["summary"])
        if summary:
            out.suggestions["summary"] = _clip(summary, 4000)
    if "experience" in sections:
        _parse_experience(sections["experience"], out)
    if "projects" in sections:
        _parse_projects(sections["projects"], out)
    if "education" in sections:
        _parse_education(sections["education"], out)
    if "certifications" in sections:
        _parse_certifications(sections["certifications"], out)
    if "skills" in sections:
        _parse_skills(sections["skills"], out)
    return out
