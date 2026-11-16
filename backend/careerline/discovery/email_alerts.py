# SPDX-License-Identifier: Apache-2.0
"""Jobs from job-alert emails (LinkedIn, Indeed, Glassdoor, ZipRecruiter, and others).

Job sites that forbid scraping still email you the jobs that match your saved searches.
This source reads those emails from a mailbox you choose, over IMAP with TLS, and turns
each job in them into a posting. It never visits the job sites themselves.

* The mailbox is opened read-only (``EXAMINE``): nothing is marked read, moved, or deleted.
* Only messages from the job sites' alert senders, from the last ``days`` days, are read.
* The IMAP server is checked like any outbound request: no private or local addresses
  unless the server allows them.
* Email HTML is untrusted: it's parsed with the standard library's tolerant parser, links
  are matched against each site's known job-link shapes, and tracking parameters are
  dropped from the links that are kept.
"""

from __future__ import annotations

import asyncio
import contextlib
import email
import email.policy
import email.utils
import imaplib
import ipaddress
import re
import socket
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from pydantic import BaseModel, Field, field_validator

from careerline.discovery.normalize import NormalizedPosting, clip
from careerline.net import SafeFetcher, is_public_address

MAX_MESSAGES = 150
IMAP_TIMEOUT = 30


@dataclass(frozen=True)
class Site:
    key: str
    label: str
    # IMAP FROM search terms (matched as substrings of the sender).
    senders: tuple[str, ...]
    # Turns a link into (job id, clean URL), or None if it isn't a job link.
    job_link: Callable[[str], tuple[str, str] | None]


def _qs(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


def _unwrap(url: str) -> str:
    """Follow common redirect wrappers (?url=, ?u=, ?dest=) to the real link."""
    for _ in range(3):
        params = _qs(url)
        for key in ("url", "u", "dest", "destination", "redirect", "target"):
            inner = params.get(key, [""])[0]
            if inner.startswith(("http://", "https://")):
                url = unquote(inner)
                break
        else:
            return url
    return url


def _linkedin(url: str) -> tuple[str, str] | None:
    m = re.search(r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?#]*-)?(\d{6,})", url)
    if not m:
        return None
    return m.group(1), f"https://www.linkedin.com/jobs/view/{m.group(1)}/"


def _indeed(url: str) -> tuple[str, str] | None:
    if "indeed." not in urlsplit(url).netloc:
        return None
    params = _qs(url)
    jk = (params.get("jk") or params.get("vjk") or [""])[0]
    if re.fullmatch(r"[0-9a-f]{12,20}", jk):
        host = urlsplit(url).netloc
        return jk, f"https://{host}/viewjob?jk={jk}"
    if "/rc/clk" in url or "/pagead/clk" in url or "/viewjob" in url:
        key = re.sub(r"\W", "", url)[-40:]
        return key, url.split("#")[0]
    return None


def _glassdoor(url: str) -> tuple[str, str] | None:
    if "glassdoor." not in urlsplit(url).netloc:
        return None
    params = _qs(url)
    jid = (params.get("jobListingId") or params.get("jl") or [""])[0]
    if not jid:
        m = re.search(r"[_-]JV.*?_KO\d+,\d+.*?(\d{8,})\.htm|jobListingId=(\d+)", url)
        jid = (m.group(1) or m.group(2)) if m else ""
    if not jid:
        return None
    return jid, f"https://www.glassdoor.com/job-listing/j?jl={jid}"


def _ziprecruiter(url: str) -> tuple[str, str] | None:
    parts = urlsplit(url)
    if "ziprecruiter." not in parts.netloc:
        return None
    m = re.search(r"/(?:jobs|job|c/[^/]+/job)/[^?#]*?([0-9a-f]{8,}|\d{6,})", parts.path)
    if not m and "/ekm/" not in parts.path and "/km/" not in parts.path:
        return None
    key = m.group(1) if m else re.sub(r"\W", "", parts.path)[-40:]
    return key, f"{parts.scheme}://{parts.netloc}{parts.path}"


def _monster(url: str) -> tuple[str, str] | None:
    parts = urlsplit(url)
    if "monster." not in parts.netloc or "/job" not in parts.path:
        return None
    return re.sub(r"\W", "", parts.path)[-40:], f"https://{parts.netloc}{parts.path}"


SITES: dict[str, Site] = {
    s.key: s
    for s in (
        Site("linkedin", "LinkedIn", ("linkedin.com",), _linkedin),
        Site("indeed", "Indeed", ("indeed.com", "indeedemail.com"), _indeed),
        Site("glassdoor", "Glassdoor", ("glassdoor.com",), _glassdoor),
        Site("ziprecruiter", "ZipRecruiter", ("ziprecruiter.com",), _ziprecruiter),
        Site("monster", "Monster", ("monster.com",), _monster),
    )
}


class EmailAlertsConfig(BaseModel):
    model_config = {"extra": "forbid", "str_strip_whitespace": True}

    host: str = Field(
        default="imap.gmail.com",
        pattern=r"^[A-Za-z0-9.-]{1,253}$",
        description="IMAP server, like imap.gmail.com or outlook.office365.com",
    )
    port: int = Field(default=993, ge=1, le=65535)
    folder: str = Field(default="INBOX", min_length=1, max_length=200)
    sites: list[str] = Field(
        default_factory=lambda: list(SITES), min_length=1, max_length=len(SITES)
    )
    days: int = Field(default=7, ge=1, le=60)

    @field_validator("sites")
    @classmethod
    def _known(cls, value: list[str]) -> list[str]:
        unknown = [s for s in value if s not in SITES]
        if unknown:
            raise ValueError(f"unknown site: {unknown[0]}")
        return list(dict.fromkeys(value))


class EmailAlertsSecret(BaseModel):
    model_config = {"extra": "forbid", "str_strip_whitespace": True}

    username: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=500)


# --- reading the mailbox ------------------------------------------------------------------


class MailboxError(Exception):
    pass


class _PinnedImap(imaplib.IMAP4_SSL):
    """IMAP over TLS to an address that was already checked, verifying ``host``'s certificate.

    Connecting to the checked address (not resolving the name again) means DNS can't be
    switched to a private address between the check and the connection."""

    def __init__(self, host: str, port: int, address: str) -> None:
        self._address = address
        super().__init__(host, port, ssl_context=ssl.create_default_context(), timeout=IMAP_TIMEOUT)

    def _create_socket(self, timeout: float | None) -> socket.socket:
        sock = socket.create_connection((self._address, self.port), timeout)
        wrapped: socket.socket = self.ssl_context.wrap_socket(sock, server_hostname=self.host)
        return wrapped


def _imap_read(
    host: str, port: int, address: str, cfg: EmailAlertsConfig, sec: EmailAlertsSecret
) -> list[bytes]:
    """Blocking IMAP read. Connects to the pre-checked ``address``, verifies TLS for ``host``."""
    try:
        conn = _PinnedImap(host, port, address)
    except (OSError, imaplib.IMAP4.error, ssl.SSLError) as exc:
        raise MailboxError(f"Couldn't connect to {host}: {exc}") from None
    try:
        try:
            conn.login(sec.username, sec.password)
        except imaplib.IMAP4.error:
            raise MailboxError(
                "The mail server rejected the sign-in. For Gmail and Outlook, use an app "
                "password, not your normal password."
            ) from None
        status, _ = conn.select(f'"{cfg.folder}"', readonly=True)
        if status != "OK":
            raise MailboxError(f"Couldn't open the folder {cfg.folder!r}.")
        since = (datetime.now(UTC) - timedelta(days=cfg.days)).strftime("%d-%b-%Y")
        ids: list[bytes] = []
        for key in cfg.sites:
            site = SITES.get(key)
            for sender in site.senders if site else ():
                status, data = conn.search(None, "SINCE", since, "FROM", f'"{sender}"')
                if status == "OK" and data and data[0]:
                    ids += data[0].split()
        out: list[bytes] = []
        for msg_id in sorted(set(ids), key=int, reverse=True)[:MAX_MESSAGES]:
            # BODY.PEEK leaves the message unread.
            status, data = conn.fetch(msg_id.decode(), "(BODY.PEEK[])")
            if status == "OK":
                out += [part[1] for part in data if isinstance(part, tuple)]
        return out
    finally:
        with contextlib.suppress(Exception):
            conn.logout()


# Tests replace this to serve messages without a server.
Reader = Callable[[str, int, str, EmailAlertsConfig, EmailAlertsSecret], list[bytes]]
_reader: Reader = _imap_read


def set_reader(reader: Reader | None) -> None:
    global _reader
    _reader = reader or _imap_read


async def _check_host(f: SafeFetcher, host: str, port: int) -> str:
    try:
        literal = ipaddress.ip_address(host)
        addresses = [str(literal)]
    except ValueError:
        try:
            addresses = await f.resolver(host, port)
        except OSError:
            raise MailboxError(f"Couldn't find the mail server {host}.") from None
    if not addresses:
        raise MailboxError(f"Couldn't find the mail server {host}.")
    if not f.allow_private and not all(is_public_address(a) for a in addresses):
        raise MailboxError(
            "That mail server is on a private network. Allow private addresses for "
            "outbound requests in the server settings to use it."
        )
    return addresses[0]


# --- parsing alert emails -------------------------------------------------------------------

_GENERIC = re.compile(
    r"^(view( (all|more|similar))?( jobs?)?|see (all|more)( jobs)?|apply( now)?|easy apply|"
    r"save|unsubscribe|manage alerts?|more jobs|new|promoted|job alert|\d+ new jobs?.*|"
    r"show more|search jobs|update your preferences|view job)$",
    re.I,
)


class _Walker(HTMLParser):
    """Flattens an email into a sequence of ("text", str) and ("link", href, text)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[tuple[str, ...]] = []
        self._href: str | None = None
        self._link_text: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("style", "script", "head", "title"):
            self._skip += 1
        if tag == "a":
            self._href = dict(attrs).get("href") or ""
            self._link_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("style", "script", "head", "title") and self._skip:
            self._skip -= 1
        if tag == "a" and self._href is not None:
            self.items.append(("link", self._href, " ".join(self._link_text).strip()))
            self._href = None

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = re.sub(r"\s+", " ", data).strip()
        if not text:
            return
        if self._href is not None:
            self._link_text.append(text)
        else:
            self.items.append(("text", text))


def _html_body(msg: EmailMessage) -> tuple[str, str]:
    html = plain = ""
    for part in msg.walk():
        ctype = part.get_content_type()
        if part.get_content_disposition() == "attachment":
            continue
        try:
            content = part.get_content()
        except (LookupError, UnicodeError, AssertionError):
            continue
        if ctype == "text/html" and not html and isinstance(content, str):
            html = content
        elif ctype == "text/plain" and not plain and isinstance(content, str):
            plain = content
    return html, plain


def _plain_to_items(plain: str) -> list[tuple[str, ...]]:
    items: list[tuple[str, ...]] = []
    for line in plain.splitlines():
        line = line.strip()
        for url in re.findall(r"https?://\S+", line):
            items.append(("link", url.rstrip(">)."), ""))
        rest = re.sub(r"<?https?://\S+>?", "", line).strip(" -:|")
        if rest:
            items.append(("text", rest))
    return items


def _looks_like_title(text: str) -> bool:
    words = text.split()
    return 1 <= len(words) <= 16 and not _GENERIC.match(text) and len(text) <= 150


def parse_alert(raw: bytes, site_keys: list[str]) -> list[NormalizedPosting]:
    """The jobs listed in one alert email."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    assert isinstance(msg, EmailMessage)  # noqa: S101
    _, address = email.utils.parseaddr(str(msg.get("From", "")))
    domain = address.rpartition("@")[2].lower().rstrip(".")
    sites = [SITES[k] for k in site_keys if k in SITES]
    # The sender's domain must be the site's domain or a subdomain of it, so look-alikes
    # such as linkedin.com.example.net don't count.
    site = next(
        (s for s in sites if any(domain == d or domain.endswith("." + d) for d in s.senders)),
        None,
    )
    if site is None:
        return []
    html, plain = _html_body(msg)
    if html:
        walker = _Walker()
        try:
            walker.feed(html)
            walker.close()
        except Exception:
            walker.items = []
        items = walker.items or _plain_to_items(plain)
    else:
        items = _plain_to_items(plain)
    received = None
    try:
        date_header = msg.get("Date")
        received = email.utils.parsedate_to_datetime(str(date_header)) if date_header else None
    except (TypeError, ValueError):
        received = None

    jobs: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    current: str | None = None
    for item in items:
        if item[0] == "link":
            found = site.job_link(_unwrap(item[1]))
            if found is None:
                current = None if item[2] and not _GENERIC.match(item[2]) else current
                continue
            job_id, url = found
            job = jobs.get(job_id)
            if job is None:
                job = {"url": url, "title": "", "context": []}
                jobs[job_id] = job
                order.append(job_id)
            text = item[2]
            if text and _looks_like_title(text) and not job["title"]:
                job["title"] = text
            elif text and not _GENERIC.match(text) and len(job["context"]) < 6:
                job["context"].append(text)
            current = job_id
        elif current is not None and len(jobs[current]["context"]) < 6:
            text = item[1]
            if not _GENERIC.match(text) and len(text) <= 200:
                jobs[current]["context"].append(text)

    out = []
    for job_id in order:
        job = jobs[job_id]
        context = [c for c in job["context"] if c != job["title"]]
        title = job["title"] or next((c for c in context if _looks_like_title(c)), "")
        if not title:
            continue
        rest = [c for c in context if c != title]
        company, location = _company_location(rest)
        snippet = "\n".join(rest[:4])
        out.append(
            NormalizedPosting(
                external_id=f"{site.key}:{job_id}"[:300],
                title=clip(title, 200),
                company=company,
                location=location,
                description=(f"{snippet}\n\n" if snippet else "")
                + f"From a {site.label} job alert email. Open the link for the full description, "
                "or paste it into the job to improve the fit score.",
                url=job["url"],
                posted_at=received,
            ).finish()
        )
    return out


def _company_location(lines: list[str]) -> tuple[str, str]:
    """Alert cards show "Company · Location" or company and location on separate lines."""
    for line in lines[:3]:
        for sep in (" \N{MIDDLE DOT} ", " \N{BULLET} ", " - ", " | ", " \N{EN DASH} "):
            if sep in line:
                left, _, right = line.partition(sep)
                return clip(left.strip(), 200), clip(right.strip(), 200)
    company = lines[0] if lines else ""
    location = lines[1] if len(lines) > 1 and len(lines[1]) <= 80 else ""
    return clip(company, 200), clip(location, 200)


async def fetch_email_alerts(
    f: SafeFetcher, config: BaseModel, secret: BaseModel
) -> list[NormalizedPosting]:
    from careerline.discovery.adapters import MAX_POSTINGS_PER_FETCH, SourceError

    assert isinstance(config, EmailAlertsConfig) and isinstance(secret, EmailAlertsSecret)  # noqa: S101
    try:
        address = await _check_host(f, config.host, config.port)
        raw_messages = await asyncio.to_thread(
            _reader, config.host, config.port, address, config, secret
        )
    except MailboxError as exc:
        raise SourceError(str(exc)) from None
    seen: set[str] = set()
    out: list[NormalizedPosting] = []
    for raw in raw_messages:
        for p in parse_alert(raw, config.sites):
            if p.external_id not in seen:
                seen.add(p.external_id)
                out.append(p)
    return out[:MAX_POSTINGS_PER_FETCH]
