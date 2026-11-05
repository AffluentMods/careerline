# SPDX-License-Identifier: Apache-2.0
"""Import one job from a URL the user pasted, or from pasted text.

The page is fetched once through the SSRF-safe fetcher, only if robots.txt
allows it, and never from sites whose terms forbid automated access (the user
is asked to paste the description instead). Structured schema.org
``JobPosting`` data is preferred; otherwise the page's text is used.
"""

from __future__ import annotations

import hashlib
import json
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

from waypoint.discovery.normalize import NormalizedPosting, clip, html_to_text, parse_datetime
from waypoint.net import FetchError, SafeFetcher, robots_allows

# Sites whose terms of service prohibit automated access. Paste the text instead.
BLOCKED_DOMAINS = (
    "linkedin.com",
    "indeed.com",
    "glassdoor.com",
    "ziprecruiter.com",
    "monster.com",
    "simplyhired.com",
    "dice.com",
    "careerbuilder.com",
)

MAX_PAGE_BYTES = 3_000_000


class ImportRefused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ld_json: list[str] = []
        self.meta: dict[str, str] = {}
        self.title = ""
        self._in_ld = False
        self._in_title = False
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._in_ld, self._buf = True, []
        elif tag == "title":
            self._in_title, self._buf = True, []
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key in {"og:title", "og:site_name", "og:description", "description"}:
                self.meta[key] = a.get("content", "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_ld:
            self.ld_json.append("".join(self._buf))
            self._in_ld = False
        elif tag == "title" and self._in_title:
            self.title = "".join(self._buf).strip()
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_ld or self._in_title:
            self._buf.append(data)


def _job_postings(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, list):
        for item in node:
            found.extend(_job_postings(item))
    elif isinstance(node, dict):
        kind = node.get("@type")
        kinds = kind if isinstance(kind, list) else [kind]
        if "JobPosting" in kinds:
            found.append(node)
        for key in ("@graph", "mainEntity", "itemListElement"):
            if key in node:
                found.extend(_job_postings(node[key]))
    return found


def _s(value: Any) -> str:
    if isinstance(value, dict):
        return _s(value.get("name") or value.get("@value") or "")
    if isinstance(value, list):
        return ", ".join(_s(v) for v in value if _s(v))
    return value if isinstance(value, str) else "" if value is None else str(value)


def _location(value: Any) -> str:
    items = value if isinstance(value, list) else [value]
    out = []
    for item in items:
        address = item.get("address") if isinstance(item, dict) else None
        if isinstance(address, dict):
            parts = [_s(address.get(k)) for k in ("addressLocality", "addressRegion")]
            text = ", ".join(p for p in parts if p) or _s(address.get("addressCountry"))
            if text:
                out.append(text)
    return "; ".join(out)


def _obj(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def from_json_ld(data: dict[str, Any], url: str) -> NormalizedPosting:
    salary = _obj(data.get("baseSalary"))
    value = _obj(salary.get("value"))
    unit = _s(value.get("unitText") or salary.get("unitText")).upper()

    def cents(v: Any) -> int | None:
        try:
            n = float(v)
        except (TypeError, ValueError):
            return None
        return round(n * 100) if n > 0 else None

    lo = cents(value.get("minValue")) or cents(value.get("value"))
    hi = cents(value.get("maxValue")) or lo
    remote = "TELECOMMUTE" in _s(data.get("jobLocationType")).upper()
    closes = parse_datetime(data.get("validThrough"))
    return NormalizedPosting(
        external_id=url,
        title=html_to_text(_s(data.get("title"))),
        company=_s(data.get("hiringOrganization")),
        location=_location(data.get("jobLocation")) or ("Remote" if remote else ""),
        work_mode="remote" if remote else None,
        employment_type=_s(data.get("employmentType")),
        pay_min_cents=lo,
        pay_max_cents=hi,
        pay_period="hour" if unit == "HOUR" else "year" if unit in {"YEAR", "ANNUAL"} else None,
        currency=_s(salary.get("currency")) or "USD",
        description=html_to_text(_s(data.get("description"))),
        url=url,
        posted_at=parse_datetime(data.get("datePosted")),
        closes_on=closes.date() if closes else None,
    ).finish()


def parse_job_page(markup: str, url: str) -> NormalizedPosting:
    parser = _PageParser()
    parser.feed(markup)
    parser.close()
    for block in parser.ld_json:
        try:
            postings = _job_postings(json.loads(block))
        except ValueError:
            continue
        if postings:
            return from_json_ld(postings[0], url)
    title = parser.meta.get("og:title") or parser.title
    return NormalizedPosting(
        external_id=url,
        title=html_to_text(title),
        company=parser.meta.get("og:site_name", ""),
        description=html_to_text(markup),
        url=url,
    ).finish()


def blocked_domain(url: str) -> str | None:
    host = (urlsplit(url).hostname or "").lower()
    for domain in BLOCKED_DOMAINS:
        if host == domain or host.endswith("." + domain):
            return domain
    return None


async def import_url(fetcher: SafeFetcher, url: str) -> NormalizedPosting:
    domain = blocked_domain(url)
    if domain:
        raise ImportRefused(
            "site_not_allowed",
            f"{domain} doesn't allow automated access. "
            "Open the posting, copy its text, and paste it here instead.",
        )
    try:
        if not await robots_allows(fetcher, url):
            raise ImportRefused(
                "robots_disallowed",
                "This site asks automated tools not to read that page. "
                "Paste the description instead.",
            )
        result = await fetcher.get(
            url, headers={"Accept": "text/html,application/xhtml+xml"}, max_bytes=MAX_PAGE_BYTES
        )
    except FetchError as exc:
        raise ImportRefused(exc.code, exc.message) from None
    if result.status >= 400:
        raise ImportRefused("fetch_failed", f"The page returned an error ({result.status}).")
    if "html" not in result.content_type.lower():
        raise ImportRefused("not_html", "That link isn't a web page.")
    return parse_job_page(result.text(), result.url)


def from_text(text: str, title: str, company: str, location: str, url: str) -> NormalizedPosting:
    digest = hashlib.sha256(f"{title}|{company}|{text[:2000]}".encode()).hexdigest()[:32]
    return NormalizedPosting(
        external_id=url or f"manual:{digest}",
        title=clip(title, 200),
        company=company,
        location=location,
        description=text.strip(),
        url=url,
    ).finish()
