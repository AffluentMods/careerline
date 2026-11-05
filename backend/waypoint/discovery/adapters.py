# SPDX-License-Identifier: Apache-2.0
"""Job source adapters. Each one calls an official API or a public feed.

Adapters never scrape pages. Each declares its config (validated), the secrets
it needs (stored encrypted), and a link to the provider's terms. Generic RSS
feeds are checked against robots.txt before fetching.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal
from urllib.parse import quote

import defusedxml.ElementTree as SafeET
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from waypoint.discovery.normalize import (
    NormalizedPosting,
    clip,
    html_to_text,
    parse_datetime,
)
from waypoint.net import FetchError, SafeFetcher, robots_allows

MAX_POSTINGS_PER_FETCH = 500

Slug = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class SourceError(Exception):
    """A fetch failed in a way worth showing the user (bad key, unknown board...)."""


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BoardConfig(_Config):
    board: str = Slug
    company: str = Field(default="", max_length=200)


class AdzunaConfig(_Config):
    country: str = Field(default="us", pattern=r"^[a-z]{2}$")
    what: str = Field(min_length=1, max_length=200)
    where: str = Field(default="", max_length=200)
    distance_km: int | None = Field(default=None, ge=1, le=500)
    max_days_old: int = Field(default=14, ge=1, le=90)


class AdzunaSecret(_Config):
    app_id: str = Field(min_length=1, max_length=100)
    app_key: str = Field(min_length=1, max_length=200)


class UsajobsConfig(_Config):
    keyword: str = Field(min_length=1, max_length=200)
    location: str = Field(default="", max_length=200)
    radius_miles: int | None = Field(default=None, ge=1, le=500)
    remote_only: bool = False


class UsajobsSecret(_Config):
    email: str = Field(min_length=3, max_length=320)  # USAJOBS requires it as the User-Agent
    api_key: str = Field(min_length=1, max_length=200)


class JsearchConfig(_Config):
    query: str = Field(min_length=1, max_length=200)
    date_posted: Literal["all", "today", "3days", "week", "month"] = "week"
    remote_only: bool = False


class JsearchSecret(_Config):
    api_key: str = Field(min_length=1, max_length=200)


class RssConfig(_Config):
    url: str = Field(pattern=r"^https?://", max_length=500)
    company: str = Field(default="", max_length=200)


class NoSecret(_Config):
    pass


Fetch = Callable[[SafeFetcher, BaseModel, BaseModel], Awaitable[list[NormalizedPosting]]]


@dataclass(frozen=True)
class AdapterInfo:
    kind: str
    label: str
    description: str
    terms_url: str
    config_model: type[BaseModel]
    secret_model: type[BaseModel]
    fetch: Fetch
    config_fields: list[dict[str, Any]] = field(default_factory=list)
    secret_fields: list[dict[str, Any]] = field(default_factory=list)


def _check(result_status: int, what: str) -> None:
    if result_status in (401, 403):
        raise SourceError(f"{what} rejected the credentials. Check the API key.")
    if result_status == 404:
        raise SourceError(f"{what} couldn't find that board or search.")
    if result_status == 429:
        raise SourceError(f"{what} is rate-limiting requests. Try a longer interval.")
    if result_status >= 400:
        raise SourceError(f"{what} returned an error ({result_status}).")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _str(value: Any) -> str:
    return value if isinstance(value, str) else "" if value is None else str(value)


def _date(value: Any) -> date | None:
    parsed = parse_datetime(value)
    return parsed.date() if parsed else None


def _cents(value: Any) -> int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number * 100) if number > 0 else None


# --- Greenhouse ----------------------------------------------------------------------------


async def fetch_greenhouse(
    f: SafeFetcher, config: BaseModel, _: BaseModel
) -> list[NormalizedPosting]:
    assert isinstance(config, BoardConfig)  # noqa: S101 - narrowed for mypy
    url = f"https://boards-api.greenhouse.io/v1/boards/{quote(config.board)}/jobs"
    result = await f.get(url, params={"content": "true"})
    _check(result.status, "Greenhouse")
    out = []
    for job in _list(_dict(result.json()).get("jobs"))[:MAX_POSTINGS_PER_FETCH]:
        job = _dict(job)
        if not job.get("id") or not job.get("title"):
            continue
        out.append(
            NormalizedPosting(
                external_id=_str(job["id"]),
                title=_str(job["title"]),
                company=config.company or _str(job.get("company_name")) or config.board,
                location=_str(_dict(job.get("location")).get("name")),
                description=html_to_text(_str(job.get("content"))),
                url=_str(job.get("absolute_url")),
                posted_at=parse_datetime(job.get("first_published") or job.get("updated_at")),
            ).finish()
        )
    return out


# --- Lever ---------------------------------------------------------------------------------


async def fetch_lever(f: SafeFetcher, config: BaseModel, _: BaseModel) -> list[NormalizedPosting]:
    assert isinstance(config, BoardConfig)  # noqa: S101
    result = await f.get(
        f"https://api.lever.co/v0/postings/{quote(config.board)}", params={"mode": "json"}
    )
    _check(result.status, "Lever")
    out = []
    for job in _list(result.json())[:MAX_POSTINGS_PER_FETCH]:
        job = _dict(job)
        if not job.get("id") or not job.get("text"):
            continue
        cats = _dict(job.get("categories"))
        salary = _dict(job.get("salaryRange"))
        interval = _str(salary.get("interval")).lower()
        workplace = _str(job.get("workplaceType")).lower()
        description = _str(job.get("descriptionPlain")) or html_to_text(
            _str(job.get("description"))
        )
        for block in _list(job.get("lists")):
            block = _dict(block)
            description += (
                f"\n\n{_str(block.get('text'))}\n{html_to_text(_str(block.get('content')))}"
            )
        out.append(
            NormalizedPosting(
                external_id=_str(job["id"]),
                title=_str(job["text"]),
                company=config.company or config.board,
                location=_str(cats.get("location")),
                work_mode={"remote": "remote", "hybrid": "hybrid", "onsite": "onsite"}.get(
                    workplace
                ),
                employment_type=_str(cats.get("commitment")),
                pay_min_cents=_cents(salary.get("min")),
                pay_max_cents=_cents(salary.get("max")),
                pay_period="hour" if "hour" in interval else "year" if salary else None,
                currency=_str(salary.get("currency")) or "USD",
                description=description.strip(),
                url=_str(job.get("hostedUrl")),
                posted_at=parse_datetime(job.get("createdAt")),
            ).finish()
        )
    return out


# --- Ashby ---------------------------------------------------------------------------------


async def fetch_ashby(f: SafeFetcher, config: BaseModel, _: BaseModel) -> list[NormalizedPosting]:
    assert isinstance(config, BoardConfig)  # noqa: S101
    result = await f.get(
        f"https://api.ashbyhq.com/posting-api/job-board/{quote(config.board)}",
        params={"includeCompensation": "true"},
    )
    _check(result.status, "Ashby")
    out = []
    for job in _list(_dict(result.json()).get("jobs"))[:MAX_POSTINGS_PER_FETCH]:
        job = _dict(job)
        if not job.get("id") or not job.get("title") or job.get("isListed") is False:
            continue
        workplace = _str(job.get("workplaceType")).lower()
        mode = (
            "remote"
            if job.get("isRemote") or workplace == "remote"
            else (
                "hybrid" if workplace == "hybrid" else "onsite" if workplace == "onsite" else None
            )
        )
        lo = hi = None
        period = None
        currency = "USD"
        comp = _dict(job.get("compensation"))
        for tier in _list(comp.get("summaryComponents")):
            tier = _dict(tier)
            if _str(tier.get("compensationType")).lower() == "salary":
                lo, hi = _cents(tier.get("minValue")), _cents(tier.get("maxValue"))
                interval = _str(tier.get("interval")).lower()
                period = "hour" if "hour" in interval else "year"
                currency = _str(tier.get("currencyCode")) or "USD"
                break
        out.append(
            NormalizedPosting(
                external_id=_str(job["id"]),
                title=_str(job["title"]),
                company=config.company or config.board,
                location=_str(job.get("location")),
                work_mode=mode,
                employment_type=_str(job.get("employmentType")),
                pay_min_cents=lo,
                pay_max_cents=hi,
                pay_period=period,
                currency=currency,
                description=_str(job.get("descriptionPlain"))
                or html_to_text(_str(job.get("descriptionHtml"))),
                url=_str(job.get("jobUrl")),
                posted_at=parse_datetime(job.get("publishedAt")),
            ).finish()
        )
    return out


# --- Adzuna --------------------------------------------------------------------------------


async def fetch_adzuna(
    f: SafeFetcher, config: BaseModel, secret: BaseModel
) -> list[NormalizedPosting]:
    assert isinstance(config, AdzunaConfig) and isinstance(secret, AdzunaSecret)  # noqa: S101
    params: dict[str, Any] = {
        "app_id": secret.app_id,
        "app_key": secret.app_key,
        "what": config.what,
        "results_per_page": 50,
        "max_days_old": config.max_days_old,
        "content-type": "application/json",
    }
    if config.where:
        params["where"] = config.where
    if config.distance_km:
        params["distance"] = config.distance_km
    out: list[NormalizedPosting] = []
    for page in (1, 2):
        result = await f.get(
            f"https://api.adzuna.com/v1/api/jobs/{config.country}/search/{page}", params=params
        )
        _check(result.status, "Adzuna")
        jobs = _list(_dict(result.json()).get("results"))
        for job in jobs:
            job = _dict(job)
            if not job.get("id") or not job.get("title"):
                continue
            predicted = _str(job.get("salary_is_predicted")) == "1"
            out.append(
                NormalizedPosting(
                    external_id=_str(job["id"]),
                    title=html_to_text(_str(job["title"])),
                    company=_str(_dict(job.get("company")).get("display_name")),
                    location=_str(_dict(job.get("location")).get("display_name")),
                    employment_type=_str(job.get("contract_time")),
                    # Adzuna's predicted salaries are estimates, not posted pay; leave them out.
                    pay_min_cents=None if predicted else _cents(job.get("salary_min")),
                    pay_max_cents=None if predicted else _cents(job.get("salary_max")),
                    pay_period=None if predicted else "year",
                    description=html_to_text(_str(job.get("description"))),
                    url=_str(job.get("redirect_url")),
                    posted_at=parse_datetime(job.get("created")),
                    lat=job.get("latitude")
                    if isinstance(job.get("latitude"), int | float)
                    else None,
                    lon=job.get("longitude")
                    if isinstance(job.get("longitude"), int | float)
                    else None,
                ).finish()
            )
        if len(jobs) < 50:
            break
    return out[:MAX_POSTINGS_PER_FETCH]


# --- USAJOBS -------------------------------------------------------------------------------


async def fetch_usajobs(
    f: SafeFetcher, config: BaseModel, secret: BaseModel
) -> list[NormalizedPosting]:
    assert isinstance(config, UsajobsConfig) and isinstance(secret, UsajobsSecret)  # noqa: S101
    params: dict[str, Any] = {"Keyword": config.keyword, "ResultsPerPage": 100}
    if config.location:
        params["LocationName"] = config.location
    if config.radius_miles:
        params["Radius"] = config.radius_miles
    if config.remote_only:
        params["RemoteIndicator"] = "True"
    result = await f.get(
        "https://data.usajobs.gov/api/search",
        params=params,
        headers={"User-Agent": secret.email, "Authorization-Key": secret.api_key},
    )
    _check(result.status, "USAJOBS")
    items = _list(_dict(_dict(result.json()).get("SearchResult")).get("SearchResultItems"))
    out = []
    for item in items[:MAX_POSTINGS_PER_FETCH]:
        d = _dict(_dict(item).get("MatchedObjectDescriptor"))
        if not d.get("PositionTitle"):
            continue
        pay = _dict((_list(d.get("PositionRemuneration")) or [{}])[0])
        rate = _str(pay.get("RateIntervalCode")).upper()
        details = _dict(_dict(d.get("UserArea")).get("Details"))
        duties = "\n".join(_str(x) for x in _list(details.get("MajorDuties")))
        description = "\n\n".join(
            part
            for part in (
                _str(details.get("JobSummary")),
                duties,
                _str(d.get("QualificationSummary")),
                f"Security clearance: {_str(details.get('SecurityClearance'))}"
                if details.get("SecurityClearance")
                else "",
            )
            if part
        )
        schedule = ", ".join(_str(_dict(s).get("Name")) for s in _list(d.get("PositionSchedule")))
        out.append(
            NormalizedPosting(
                external_id=_str(_dict(item).get("MatchedObjectId") or d.get("PositionID")),
                title=_str(d.get("PositionTitle")),
                company=_str(d.get("OrganizationName")) or _str(d.get("DepartmentName")),
                location=_str(d.get("PositionLocationDisplay")),
                work_mode="remote"
                if _str(details.get("RemoteIndicator")).lower() == "true"
                else None,
                employment_type=schedule,
                pay_min_cents=_cents(pay.get("MinimumRange")),
                pay_max_cents=_cents(pay.get("MaximumRange")),
                pay_period="hour" if rate == "PH" else "year" if pay else None,
                description=description,
                url=_str(d.get("PositionURI")),
                posted_at=parse_datetime(d.get("PublicationStartDate")),
                closes_on=_date(d.get("ApplicationCloseDate")),
            ).finish()
        )
    return out


# --- JSearch (RapidAPI) ----------------------------------------------------------------------


async def fetch_jsearch(
    f: SafeFetcher, config: BaseModel, secret: BaseModel
) -> list[NormalizedPosting]:
    assert isinstance(config, JsearchConfig) and isinstance(secret, JsearchSecret)  # noqa: S101
    params: dict[str, Any] = {
        "query": config.query,
        "page": 1,
        "num_pages": 1,
        "date_posted": config.date_posted,
    }
    if config.remote_only:
        params["remote_jobs_only"] = "true"
    result = await f.get(
        "https://jsearch.p.rapidapi.com/search",
        params=params,
        headers={"X-RapidAPI-Key": secret.api_key, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"},
    )
    _check(result.status, "JSearch")
    out = []
    for job in _list(_dict(result.json()).get("data"))[:MAX_POSTINGS_PER_FETCH]:
        job = _dict(job)
        if not job.get("job_id") or not job.get("job_title"):
            continue
        period = _str(job.get("job_salary_period")).upper()
        location = ", ".join(
            p for p in (_str(job.get("job_city")), _str(job.get("job_state"))) if p
        )
        out.append(
            NormalizedPosting(
                external_id=_str(job["job_id"]),
                title=_str(job["job_title"]),
                company=_str(job.get("employer_name")),
                location=location or _str(job.get("job_country")),
                work_mode="remote" if job.get("job_is_remote") is True else None,
                employment_type=_str(job.get("job_employment_type")),
                pay_min_cents=_cents(job.get("job_min_salary")),
                pay_max_cents=_cents(job.get("job_max_salary")),
                pay_period="hour" if period == "HOUR" else "year" if period == "YEAR" else None,
                description=_str(job.get("job_description")),
                url=_str(job.get("job_apply_link")),
                posted_at=parse_datetime(job.get("job_posted_at_datetime_utc")),
            ).finish()
        )
    return out


# --- RSS / Atom --------------------------------------------------------------------------------


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(node: Any, *names: str) -> str:
    for child in node:
        if _local(child.tag) in names:
            if _local(child.tag) == "link" and child.get("href"):
                return str(child.get("href"))
            return "".join(child.itertext()).strip()
    return ""


def parse_feed(body: bytes, company: str = "") -> list[NormalizedPosting]:
    try:
        root = SafeET.fromstring(body)
    except Exception:  # defusedxml raises several types for hostile or broken XML
        raise SourceError("The feed isn't valid RSS or Atom.") from None
    items = [n for n in root.iter() if _local(n.tag) in ("item", "entry")]
    out = []
    for node in items[:MAX_POSTINGS_PER_FETCH]:
        title = _child_text(node, "title")
        link = _child_text(node, "link")
        if not title:
            continue
        guid = _child_text(node, "guid", "id") or link or title
        description = _child_text(node, "description", "summary", "content", "encoded")
        out.append(
            NormalizedPosting(
                external_id=guid[:300],
                title=html_to_text(title),
                company=company or html_to_text(_child_text(node, "author", "creator", "company")),
                location=html_to_text(_child_text(node, "location")),
                description=html_to_text(description),
                url=link,
                posted_at=parse_datetime(
                    _child_text(node, "pubdate", "published", "updated", "date")
                ),
            ).finish()
        )
    return out


async def fetch_rss(f: SafeFetcher, config: BaseModel, _: BaseModel) -> list[NormalizedPosting]:
    assert isinstance(config, RssConfig)  # noqa: S101
    if not await robots_allows(f, config.url):
        raise SourceError("This site's robots.txt doesn't allow automated access to the feed.")
    result = await f.get(
        config.url,
        headers={"Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"},
    )
    _check(result.status, "The feed")
    return parse_feed(result.body, config.company)


# --- registry ------------------------------------------------------------------------------

_BOARD_FIELDS: list[dict[str, Any]] = [
    {
        "name": "board",
        "label": "Board name",
        "required": True,
        "hint": "The company's board token from its job-board URL",
    },
    {"name": "company", "label": "Company name (optional)", "hint": "Shown on postings"},
]

ADAPTERS: dict[str, AdapterInfo] = {
    a.kind: a
    for a in [
        AdapterInfo(
            "greenhouse",
            "Greenhouse job board",
            "A company's public Greenhouse board (boards.greenhouse.io/<board>).",
            "https://developers.greenhouse.io/job-board.html",
            BoardConfig,
            NoSecret,
            fetch_greenhouse,
            _BOARD_FIELDS,
        ),
        AdapterInfo(
            "lever",
            "Lever job board",
            "A company's public Lever postings (jobs.lever.co/<company>).",
            "https://github.com/lever/postings-api",
            BoardConfig,
            NoSecret,
            fetch_lever,
            _BOARD_FIELDS,
        ),
        AdapterInfo(
            "ashby",
            "Ashby job board",
            "A company's public Ashby board (jobs.ashbyhq.com/<board>).",
            "https://developers.ashbyhq.com/docs/public-job-posting-api",
            BoardConfig,
            NoSecret,
            fetch_ashby,
            _BOARD_FIELDS,
        ),
        AdapterInfo(
            "adzuna",
            "Adzuna search",
            "Keyword and location search across many job boards. Needs a free Adzuna key.",
            "https://developer.adzuna.com/",
            AdzunaConfig,
            AdzunaSecret,
            fetch_adzuna,
            [
                {"name": "what", "label": "Keywords", "required": True},
                {"name": "where", "label": "Location (optional)"},
                {"name": "country", "label": "Country code", "hint": "Two letters, like us or gb"},
                {"name": "distance_km", "label": "Distance in km (optional)", "type": "number"},
                {"name": "max_days_old", "label": "Max age in days", "type": "number"},
            ],
            [
                {"name": "app_id", "label": "Application ID", "required": True},
                {"name": "app_key", "label": "Application key", "required": True, "secret": True},
            ],
        ),
        AdapterInfo(
            "usajobs",
            "USAJOBS search",
            "U.S. federal jobs from the official USAJOBS API. Needs a free API key.",
            "https://developer.usajobs.gov/",
            UsajobsConfig,
            UsajobsSecret,
            fetch_usajobs,
            [
                {"name": "keyword", "label": "Keywords", "required": True},
                {"name": "location", "label": "Location (optional)"},
                {"name": "radius_miles", "label": "Radius in miles (optional)", "type": "number"},
                {"name": "remote_only", "label": "Remote only", "type": "checkbox"},
            ],
            [
                {"name": "email", "label": "Email registered with USAJOBS", "required": True},
                {"name": "api_key", "label": "API key", "required": True, "secret": True},
            ],
        ),
        AdapterInfo(
            "jsearch",
            "JSearch (RapidAPI)",
            "Aggregated search through the JSearch API on RapidAPI. Needs a RapidAPI key.",
            "https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch",
            JsearchConfig,
            JsearchSecret,
            fetch_jsearch,
            [
                {
                    "name": "query",
                    "label": "Search",
                    "required": True,
                    "hint": "For example: security engineer in Seattle, WA",
                },
                {
                    "name": "date_posted",
                    "label": "Posted within",
                    "type": "select",
                    "options": ["all", "today", "3days", "week", "month"],
                },
                {"name": "remote_only", "label": "Remote only", "type": "checkbox"},
            ],
            [{"name": "api_key", "label": "RapidAPI key", "required": True, "secret": True}],
        ),
        AdapterInfo(
            "rss",
            "RSS or Atom feed",
            "Any public job feed. Checked against the site's robots.txt before each fetch.",
            "https://www.rfc-editor.org/rfc/rfc9309",
            RssConfig,
            NoSecret,
            fetch_rss,
            [
                {"name": "url", "label": "Feed URL", "required": True},
                {"name": "company", "label": "Company name (optional)"},
            ],
        ),
    ]
}


def parse_config(kind: str, raw: dict[str, Any]) -> BaseModel:
    return ADAPTERS[kind].config_model.model_validate(raw)


def parse_secret(kind: str, raw: dict[str, Any]) -> BaseModel:
    return ADAPTERS[kind].secret_model.model_validate(raw)


async def run_adapter(
    fetcher: SafeFetcher, kind: str, config: dict[str, Any], secret: dict[str, Any]
) -> list[NormalizedPosting]:
    info = ADAPTERS[kind]
    try:
        cfg = info.config_model.model_validate(config)
        sec = info.secret_model.model_validate(secret)
    except ValidationError:
        raise SourceError(
            "This source's settings are incomplete. Edit the source and save it again."
        ) from None
    try:
        return await info.fetch(fetcher, cfg, sec)
    except FetchError as exc:
        raise SourceError(exc.message) from None


def describe(text: str) -> str:
    return clip(re.sub(r"\s+", " ", text), 300)
