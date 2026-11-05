# SPDX-License-Identifier: Apache-2.0
"""Turn source-specific job data into one normalized shape.

All text from sources is untrusted. It is converted to plain text here and is
only ever displayed as plain text (never as HTML).
"""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import ClassVar

MAX_DESCRIPTION = 50_000

CLEARANCE_LEVELS = ("public_trust", "secret", "top_secret")
SHIFTS = ("day", "early", "evening", "night", "weekend", "rotating", "on_call")

_DASH = "(?:-|\u2013|\u2014|to)"


@dataclass
class NormalizedPosting:
    external_id: str
    title: str
    company: str = ""
    location: str = ""
    work_mode: str | None = None  # onsite / hybrid / remote
    pay_min_cents: int | None = None
    pay_max_cents: int | None = None
    pay_period: str | None = None  # hour / year
    currency: str = "USD"
    description: str = ""
    url: str = ""
    posted_at: datetime | None = None
    closes_on: date | None = None
    employment_type: str = ""
    lat: float | None = None
    lon: float | None = None
    shifts: list[str] = field(default_factory=list)
    clearance: str | None = None

    def finish(self) -> NormalizedPosting:
        """Clip fields and fill in what can be detected from the text."""
        self.title = clip(self.title, 200) or "Untitled job"
        self.company = clip(self.company, 200)
        self.location = clip(self.location, 200)
        self.description = self.description[:MAX_DESCRIPTION]
        self.employment_type = clip(self.employment_type, 50)
        self.url = self.url[:500] if self.url.startswith(("https://", "http://")) else ""
        self.currency = (self.currency or "USD")[:3].upper()
        blob = f"{self.title}\n{self.location}\n{self.description}"
        self.work_mode = self.work_mode or detect_work_mode(blob)
        self.shifts = self.shifts or detect_shifts(blob)
        self.clearance = self.clearance or detect_clearance(blob)
        if self.pay_min_cents is None and self.pay_max_cents is None:
            pay = parse_pay(f"{self.title}\n{self.description}")
            if pay:
                self.pay_min_cents, self.pay_max_cents, self.pay_period = pay
        lo, hi = self.pay_min_cents, self.pay_max_cents
        if lo is not None and hi is not None and lo > hi:
            self.pay_min_cents, self.pay_max_cents = hi, lo
        if (lo is not None or hi is not None) and not self.pay_period:
            self.pay_period = guess_period(hi or lo or 0)
        return self


def clip(value: str | None, limit: int) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    return text[:limit].rstrip()


class _TextExtractor(HTMLParser):
    _BLOCK: ClassVar[frozenset[str]] = frozenset(
        {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section"}
    )
    _SKIP: ClassVar[frozenset[str]] = frozenset(
        {"script", "style", "noscript", "template", "svg", "head", "iframe"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n• ")
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    """Plain text from HTML. Handles HTML that arrives entity-escaped (Greenhouse does this)."""
    if "&lt;" in markup and "<" not in markup:
        markup = html.unescape(markup)
    parser = _TextExtractor()
    parser.feed(markup)
    parser.close()
    text = "".join(parser.parts).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:MAX_DESCRIPTION]


def _norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


_COMPANY_SUFFIX = re.compile(r"\b(inc|llc|ltd|corp|corporation|co|company|plc|gmbh)\b")
_REMOTE_LOC = re.compile(r"\b(remote|anywhere|work from home|wfh)\b")


def fingerprint(company: str, title: str, location: str) -> str:
    """Same job from different sources -> same fingerprint (case, punctuation, suffixes ignored)."""
    c = re.sub(r"\s+", " ", _COMPANY_SUFFIX.sub("", _norm(company))).strip()
    t = _norm(title)
    loc = _norm(location)
    loc = "remote" if _REMOTE_LOC.search(loc) else (loc.split(" ")[0] if loc else "")
    return hashlib.sha256(f"{c}|{t}|{loc}".encode()).hexdigest()[:40]


_REMOTE_PHRASES = re.compile(
    r"\b(fully remote|100% remote|remote[- ]first|work from home|"
    r"remote (position|role|job|opportunity|eligible|ok))\b"
)


def detect_work_mode(text: str) -> str | None:
    t = text.lower()
    if re.search(r"\bhybrid\b", t):
        return "hybrid"
    if _REMOTE_PHRASES.search(t) or re.search(r"(^|[\n,(|/:.-]) ?remote\b", t):
        return "remote"
    if re.search(r"\b(on[- ]?site|in[- ]office|in person)\b", t):
        return "onsite"
    return None


_SHIFT_PATTERNS: dict[str, str] = {
    "early": (
        r"\b(early (morning |day )?shift|starts? at [4-6](:\d\d)? ?a\.?m"
        rf"|[4-6](:\d\d)? ?a\.?m\.? ?{_DASH} ?\d)"
    ),
    "day": rf"\b(day shift|first shift|1st shift|business hours|9 ?(am)? ?{_DASH} ?5\b)",
    "evening": r"\b(evening shift|swing shift|second shift|2nd shift)\b",
    "night": r"\b(night shift|overnight|graveyard|third shift|3rd shift)\b",
    "weekend": r"\b(weekend shift|weekends required|work(ing)? weekends|saturday and sunday)\b",
    "rotating": r"\b(rotating shifts?|shift rotation|rotating schedule|24x7 rotation)\b",
    "on_call": r"\b(on[- ]call)\b",
}


def detect_shifts(text: str) -> list[str]:
    t = text.lower()
    return [name for name, pattern in _SHIFT_PATTERNS.items() if re.search(pattern, t)]


_CLEARANCE_PATTERNS: list[tuple[str, str]] = [
    ("top_secret", r"\b(top secret|ts/sci|ts sci|ts-sci|tssci)\b"),
    ("secret", r"\b(secret clearance|secret security clearance|active secret|dod secret)\b"),
    ("public_trust", r"\b(public trust)\b"),
]


def detect_clearance(text: str) -> str | None:
    t = text.lower()
    for level, pattern in _CLEARANCE_PATTERNS:
        if re.search(pattern, t):
            return level
    if re.search(
        r"\b(security clearance|clearance (is )?required|must be able to obtain (a )?clearance)\b",
        t,
    ):
        return "secret"
    return None


def clearance_rank(level: str | None) -> int:
    return 0 if level is None or level == "none" else CLEARANCE_LEVELS.index(level) + 1


_MONEY = r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?\s?(k|K)?"
_UNIT = r"(?:\s*(?:per|/|an|a)\s*(hour|hr|year|yr|annum|annually))"
_PAY_RANGE = re.compile(_MONEY + rf"\s*{_DASH}\s*" + _MONEY + _UNIT + "?")
_PAY_SINGLE = re.compile(_MONEY + _UNIT)


def _cents(whole: str, frac: str | None, k: str | None) -> int:
    value = int(whole.replace(",", "")) * 100 + int((frac or "0").ljust(2, "0"))
    return value * 1000 if k else value


def guess_period(cents: int) -> str:
    return "hour" if cents < 300_00 else "year"


def parse_pay(text: str) -> tuple[int, int, str] | None:
    """Find a pay range like "$50 - $65 per hour" or "$110,000 - $130,000"."""
    m = _PAY_RANGE.search(text)
    if m:
        lo = _cents(m.group(1), m.group(2), m.group(3))
        hi = _cents(m.group(4), m.group(5), m.group(6))
        unit = (m.group(7) or "").lower()
        period = "hour" if unit in {"hour", "hr"} else "year" if unit else guess_period(hi)
        if lo > 0 and hi >= lo:
            return lo, hi, period
    m = _PAY_SINGLE.search(text)
    if m:
        value = _cents(m.group(1), m.group(2), m.group(3))
        unit = m.group(4).lower()
        return value, value, "hour" if unit in {"hour", "hr"} else "year"
    return None


def hourly_cents(cents: int, period: str | None) -> int:
    return cents if period == "hour" else round(cents / 2080)


def parse_datetime(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        # Lever sends milliseconds since the epoch.
        seconds = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds, tz=UTC)
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
