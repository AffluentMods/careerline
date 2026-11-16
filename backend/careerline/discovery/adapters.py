# SPDX-License-Identifier: Apache-2.0
"""Job source adapters. Each one calls an official API or a public feed.

Adapters never scrape pages. Each declares its config (validated), the secrets
it needs (stored encrypted), and a link to the provider's terms. Generic RSS
feeds are checked against robots.txt before fetching.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Literal
from urllib.parse import quote

import defusedxml.ElementTree as SafeET
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from careerline.discovery import email_alerts
from careerline.discovery.normalize import (
    NormalizedPosting,
    clip,
    html_to_text,
    parse_datetime,
)
from careerline.net import FetchError, SafeFetcher, robots_allows

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


class KeywordConfig(_Config):
    """For boards that list everything: CareerLine keeps only the postings that match."""

    keywords: str = Field(
        default="",
        max_length=300,
        description="Comma-separated; a posting is kept if it mentions any of them.",
    )
    location: str = Field(default="", max_length=100)


class HnConfig(_Config):
    keywords: str = Field(min_length=1, max_length=300)
    remote_only: bool = False


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
    # "company" (one employer's board), "search", "remote", "community", or "inbox".
    group: str = "company"
    # Some providers ask to be checked only a few times a day.
    min_interval_hours: int = 1
    # Where to find the board name or key, shown in the add-source form.
    how_to: str = ""


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


# --- more company boards -------------------------------------------------------------------


def _phrases(keywords: str) -> list[str]:
    return [p.strip().lower() for p in keywords.split(",") if p.strip()]


def keep(p: NormalizedPosting, keywords: str, location: str = "") -> bool:
    """Whether a posting from a list-everything board matches the user's keywords/location."""
    phrases = _phrases(keywords)
    blob = f"{p.title}\n{p.description}".lower()
    if phrases and not any(
        re.search(rf"(?<![a-z0-9]){re.escape(ph)}(?![a-z0-9])", blob) for ph in phrases
    ):
        return False
    return not location.strip() or location.strip().lower() in p.location.lower()


async def fetch_workable(
    f: SafeFetcher, config: BaseModel, _: BaseModel
) -> list[NormalizedPosting]:
    assert isinstance(config, BoardConfig)  # noqa: S101
    result = await f.get(
        f"https://apply.workable.com/api/v1/widget/accounts/{quote(config.board)}",
        params={"details": "true"},
    )
    _check(result.status, "Workable")
    data = _dict(result.json())
    out = []
    for job in _list(data.get("jobs"))[:MAX_POSTINGS_PER_FETCH]:
        job = _dict(job)
        if not job.get("shortcode") or not job.get("title"):
            continue
        location = ", ".join(
            p
            for p in (_str(job.get("city")), _str(job.get("state")), _str(job.get("country")))
            if p
        )
        out.append(
            NormalizedPosting(
                external_id=_str(job["shortcode"]),
                title=_str(job["title"]),
                company=config.company or _str(data.get("name")) or config.board,
                location=location,
                work_mode="remote" if job.get("telecommuting") is True else None,
                employment_type=_str(job.get("employment_type")),
                description=html_to_text(_str(job.get("description"))),
                url=_str(job.get("url") or job.get("shortlink")),
                posted_at=parse_datetime(job.get("published_on") or job.get("created_at")),
            ).finish()
        )
    return out


SMARTRECRUITERS_DETAILS = 60


async def fetch_smartrecruiters(
    f: SafeFetcher, config: BaseModel, _: BaseModel
) -> list[NormalizedPosting]:
    assert isinstance(config, BoardConfig)  # noqa: S101
    base = f"https://api.smartrecruiters.com/v1/companies/{quote(config.board)}/postings"
    result = await f.get(base, params={"limit": 100})
    _check(result.status, "SmartRecruiters")
    out = []
    for job in _list(_dict(result.json()).get("content"))[:SMARTRECRUITERS_DETAILS]:
        job = _dict(job)
        if not job.get("id") or not job.get("name"):
            continue
        loc = _dict(job.get("location"))
        description = ""
        detail = await f.get(f"{base}/{quote(_str(job['id']))}")
        if detail.status == 200:
            sections = _dict(_dict(_dict(detail.json()).get("jobAd")).get("sections"))
            description = "\n\n".join(
                html_to_text(_str(_dict(sections.get(k)).get("text")))
                for k in ("jobDescription", "qualifications", "additionalInformation")
                if _dict(sections.get(k)).get("text")
            )
        mode = "remote" if loc.get("remote") else "hybrid" if loc.get("hybrid") else None
        out.append(
            NormalizedPosting(
                external_id=_str(job["id"]),
                title=_str(job["name"]),
                company=config.company or _str(_dict(job.get("company")).get("name")),
                location=_str(loc.get("fullLocation")).replace(", ,", ","),
                work_mode=mode,
                employment_type=_str(_dict(job.get("typeOfEmployment")).get("label")),
                description=description,
                url=f"https://jobs.smartrecruiters.com/{quote(config.board)}/{quote(_str(job['id']))}",
                posted_at=parse_datetime(job.get("releasedDate")),
            ).finish()
        )
    return out


async def fetch_recruitee(
    f: SafeFetcher, config: BaseModel, _: BaseModel
) -> list[NormalizedPosting]:
    assert isinstance(config, BoardConfig)  # noqa: S101
    result = await f.get(f"https://{quote(config.board)}.recruitee.com/api/offers/")
    _check(result.status, "Recruitee")
    out = []
    for job in _list(_dict(result.json()).get("offers"))[:MAX_POSTINGS_PER_FETCH]:
        job = _dict(job)
        if (
            not job.get("id")
            or not job.get("title")
            or job.get("status") not in (None, "published")
        ):
            continue
        salary = _dict(job.get("salary"))
        period = _str(salary.get("period")).lower()
        mode = "remote" if job.get("remote") else "hybrid" if job.get("hybrid") else None
        out.append(
            NormalizedPosting(
                external_id=_str(job["id"]),
                title=_str(job["title"]),
                company=config.company or _str(job.get("company_name")) or config.board,
                location=_str(job.get("location")),
                work_mode=mode,
                employment_type=_str(job.get("employment_type_code")).replace("_", " "),
                pay_min_cents=_cents(salary.get("min")),
                pay_max_cents=_cents(salary.get("max")),
                pay_period="hour" if "hour" in period else "year" if salary.get("min") else None,
                currency=_str(salary.get("currency")) or "USD",
                description=html_to_text(
                    _str(job.get("description")) + "\n" + _str(job.get("requirements"))
                ),
                url=_str(job.get("careers_url")),
                posted_at=parse_datetime(job.get("published_at") or job.get("created_at")),
            ).finish()
        )
    return out


async def fetch_personio(
    f: SafeFetcher, config: BaseModel, _: BaseModel
) -> list[NormalizedPosting]:
    assert isinstance(config, BoardConfig)  # noqa: S101
    result = await f.get(f"https://{quote(config.board)}.jobs.personio.de/xml")
    _check(result.status, "Personio")
    try:
        root = SafeET.fromstring(result.body)
    except Exception:
        raise SourceError("Personio returned something that isn't a job feed.") from None
    out = []
    for pos in root.iter("position"):
        pid, name = _child_text(pos, "id"), _child_text(pos, "name")
        if not pid or not name:
            continue
        parts = []
        for jd in pos.iter("jobDescription"):
            heading, value = _child_text(jd, "name"), _child_text(jd, "value")
            parts.append(f"{heading}\n{html_to_text(value)}" if heading else html_to_text(value))
        out.append(
            NormalizedPosting(
                external_id=pid,
                title=name,
                company=config.company or config.board,
                location=_child_text(pos, "office"),
                employment_type=_child_text(pos, "schedule") or _child_text(pos, "employmenttype"),
                description="\n\n".join(p for p in parts if p),
                url=f"https://{quote(config.board)}.jobs.personio.de/job/{quote(pid)}",
                posted_at=parse_datetime(_child_text(pos, "createdat")),
            ).finish()
        )
    return out[:MAX_POSTINGS_PER_FETCH]


# --- remote-job boards -----------------------------------------------------------------------


def _salary_text(text: str) -> tuple[int | None, int | None]:
    """'$85k - $155k' -> (8_500_000, 15_500_000) cents, for boards that give pay as text."""
    nums = []
    for m in re.finditer(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s?([kK])?", text):
        value = float(m.group(1).replace(",", "")) * (1000 if m.group(2) else 1)
        nums.append(round(value * 100))
    if not nums:
        return None, None
    return nums[0], nums[1] if len(nums) > 1 else None


async def fetch_remotive(
    f: SafeFetcher, config: BaseModel, _: BaseModel
) -> list[NormalizedPosting]:
    assert isinstance(config, KeywordConfig)  # noqa: S101
    phrases = _phrases(config.keywords)
    params = {"search": phrases[0]} if len(phrases) == 1 else {}
    result = await f.get("https://remotive.com/api/remote-jobs", params=params)
    _check(result.status, "Remotive")
    out = []
    for job in _list(_dict(result.json()).get("jobs")):
        job = _dict(job)
        if not job.get("id") or not job.get("title"):
            continue
        lo, hi = _salary_text(_str(job.get("salary")))
        p = NormalizedPosting(
            external_id=_str(job["id"]),
            title=_str(job["title"]),
            company=_str(job.get("company_name")),
            location=_str(job.get("candidate_required_location")) or "Remote",
            work_mode="remote",
            employment_type=_str(job.get("job_type")).replace("_", " "),
            pay_min_cents=lo,
            pay_max_cents=hi,
            pay_period="year" if lo else None,
            description=html_to_text(_str(job.get("description"))) + "\n\n(Source: Remotive)",
            url=_str(job.get("url")),
            posted_at=parse_datetime(job.get("publication_date")),
        ).finish()
        if keep(p, config.keywords, config.location):
            out.append(p)
    return out[:MAX_POSTINGS_PER_FETCH]


async def fetch_remoteok(
    f: SafeFetcher, config: BaseModel, _: BaseModel
) -> list[NormalizedPosting]:
    assert isinstance(config, KeywordConfig)  # noqa: S101
    result = await f.get("https://remoteok.com/api")
    _check(result.status, "Remote OK")
    out = []
    for job in _list(result.json()):
        job = _dict(job)
        if not job.get("id") or not job.get("position"):
            continue  # the first entry is the API's legal notice
        p = NormalizedPosting(
            external_id=_str(job["id"]),
            title=_str(job["position"]),
            company=_str(job.get("company")),
            location=_str(job.get("location")) or "Remote",
            work_mode="remote",
            pay_min_cents=_cents(job.get("salary_min")),
            pay_max_cents=_cents(job.get("salary_max")),
            pay_period="year" if _cents(job.get("salary_min")) else None,
            description=html_to_text(_str(job.get("description"))) + "\n\n(Source: Remote OK)",
            url=_str(job.get("url")),
            posted_at=parse_datetime(job.get("date")),
        ).finish()
        if keep(p, config.keywords, config.location):
            out.append(p)
    return out[:MAX_POSTINGS_PER_FETCH]


async def fetch_arbeitnow(
    f: SafeFetcher, config: BaseModel, _: BaseModel
) -> list[NormalizedPosting]:
    assert isinstance(config, KeywordConfig)  # noqa: S101
    out: list[NormalizedPosting] = []
    for page in (1, 2, 3):
        result = await f.get("https://www.arbeitnow.com/api/job-board-api", params={"page": page})
        _check(result.status, "Arbeitnow")
        data = _dict(result.json())
        for job in _list(data.get("data")):
            job = _dict(job)
            if not job.get("slug") or not job.get("title"):
                continue
            p = NormalizedPosting(
                external_id=_str(job["slug"]),
                title=_str(job["title"]),
                company=_str(job.get("company_name")),
                location=_str(job.get("location")),
                work_mode="remote" if job.get("remote") is True else None,
                employment_type=", ".join(_str(t) for t in _list(job.get("job_types"))[:2]),
                description=html_to_text(_str(job.get("description"))),
                url=_str(job.get("url")),
                posted_at=parse_datetime(job.get("created_at")),
            ).finish()
            if keep(p, config.keywords, config.location):
                out.append(p)
        if not _dict(data.get("links")).get("next"):
            break
    return out[:MAX_POSTINGS_PER_FETCH]


async def fetch_himalayas(
    f: SafeFetcher, config: BaseModel, _: BaseModel
) -> list[NormalizedPosting]:
    assert isinstance(config, KeywordConfig)  # noqa: S101
    phrases = _phrases(config.keywords) or [""]
    out: list[NormalizedPosting] = []
    seen: set[str] = set()
    for phrase in phrases[:5]:
        for page in (1, 2, 3):
            if phrase:
                result = await f.get(
                    "https://himalayas.app/jobs/api/search", params={"q": phrase, "page": page}
                )
            else:
                result = await f.get(
                    "https://himalayas.app/jobs/api",
                    params={"limit": 20, "offset": (page - 1) * 20},
                )
            _check(result.status, "Himalayas")
            jobs = _list(_dict(result.json()).get("jobs"))
            for job in jobs:
                job = _dict(job)
                guid = _str(job.get("guid") or job.get("applicationLink"))
                if not guid or not job.get("title") or guid in seen:
                    continue
                seen.add(guid)
                period = _str(job.get("salaryPeriod")).lower()
                p = NormalizedPosting(
                    external_id=guid[:300],
                    title=_str(job["title"]),
                    company=_str(job.get("companyName")),
                    location=", ".join(_str(x) for x in _list(job.get("locationRestrictions")))
                    or "Remote",
                    work_mode="remote",
                    employment_type=_str(job.get("employmentType")),
                    pay_min_cents=_cents(job.get("minSalary")),
                    pay_max_cents=_cents(job.get("maxSalary")),
                    pay_period="hour"
                    if "hour" in period
                    else "year"
                    if job.get("minSalary")
                    else None,
                    currency=_str(job.get("currency")) or "USD",
                    description=html_to_text(_str(job.get("description")))
                    + "\n\n(Source: Himalayas)",
                    url=_str(job.get("applicationLink")),
                    posted_at=parse_datetime(job.get("pubDate")),
                ).finish()
                if keep(p, "", config.location):
                    out.append(p)
            if len(jobs) < 20:
                break
    return out[:MAX_POSTINGS_PER_FETCH]


async def fetch_jobicy(f: SafeFetcher, config: BaseModel, _: BaseModel) -> list[NormalizedPosting]:
    assert isinstance(config, KeywordConfig)  # noqa: S101
    phrases = _phrases(config.keywords) or [""]
    out: list[NormalizedPosting] = []
    seen: set[str] = set()
    for phrase in phrases[:5]:
        params: dict[str, Any] = {"count": 50}
        if phrase:
            params["tag"] = phrase
        if config.location:
            params["geo"] = config.location.lower()
        result = await f.get("https://jobicy.com/api/v2/remote-jobs", params=params)
        _check(result.status, "Jobicy")
        for job in _list(_dict(result.json()).get("jobs")):
            job = _dict(job)
            jid = _str(job.get("id"))
            if not jid or not job.get("jobTitle") or jid in seen:
                continue
            seen.add(jid)
            period = _str(job.get("salaryPeriod")).lower()
            out.append(
                NormalizedPosting(
                    external_id=jid,
                    title=html_to_text(_str(job["jobTitle"])),
                    company=_str(job.get("companyName")),
                    location=_str(job.get("jobGeo")) or "Remote",
                    work_mode="remote",
                    employment_type=", ".join(_str(t) for t in _list(job.get("jobType"))),
                    pay_min_cents=_cents(job.get("salaryMin")),
                    pay_max_cents=_cents(job.get("salaryMax")),
                    pay_period="hour"
                    if "hour" in period
                    else "year"
                    if job.get("salaryMin")
                    else None,
                    currency=_str(job.get("salaryCurrency")) or "USD",
                    description=html_to_text(_str(job.get("jobDescription")))
                    + "\n\n(Source: Jobicy)",
                    url=_str(job.get("url")),
                    posted_at=parse_datetime(job.get("pubDate")),
                ).finish()
            )
    return out[:MAX_POSTINGS_PER_FETCH]


# --- Hacker News "Who is hiring?" -----------------------------------------------------------

HN_API = "https://hn.algolia.com/api/v1"


async def fetch_hn_hiring(
    f: SafeFetcher, config: BaseModel, _: BaseModel
) -> list[NormalizedPosting]:
    """Top-level comments in this month's "Ask HN: Who is hiring?" thread, by keyword."""
    assert isinstance(config, HnConfig)  # noqa: S101
    stories = await f.get(
        f"{HN_API}/search_by_date",
        params={"tags": "story,author_whoishiring", "hitsPerPage": 10},
    )
    _check(stories.status, "Hacker News")
    story = next(
        (
            _dict(h)
            for h in _list(_dict(stories.json()).get("hits"))
            if _str(_dict(h).get("title")).startswith("Ask HN: Who is hiring?")
        ),
        None,
    )
    if story is None:
        return []
    story_id = _str(story.get("objectID"))
    out: list[NormalizedPosting] = []
    for page in range(3):
        result = await f.get(
            f"{HN_API}/search",
            params={"tags": f"comment,story_{story_id}", "hitsPerPage": 1000, "page": page},
        )
        _check(result.status, "Hacker News")
        data = _dict(result.json())
        for hit in _list(data.get("hits")):
            hit = _dict(hit)
            if _str(hit.get("parent_id")) != story_id or not hit.get("comment_text"):
                continue  # replies, not job posts
            text = html_to_text(_str(hit["comment_text"]))
            first = text.splitlines()[0] if text else ""
            parts = [p.strip() for p in first.split("|") if p.strip()]
            if len(parts) < 2:
                continue  # not in the thread's "Company | Role | Location" format
            remote = "remote" in first.lower()
            location = next(
                (p for p in parts[2:] if not re.search(r"\b(full|part)[- ]?time\b|\$", p, re.I)),
                "Remote" if remote else "",
            )
            p = NormalizedPosting(
                external_id=f"hn-{hit.get('objectID')}",
                title=parts[1][:200],
                company=parts[0][:200],
                location=location,
                work_mode="remote" if remote else None,
                description=text + "\n\n(Source: Hacker News, Who is hiring?)",
                url=f"https://news.ycombinator.com/item?id={_str(hit.get('objectID'))}",
                posted_at=parse_datetime(hit.get("created_at")),
            ).finish()
            if keep(p, config.keywords) and (not config.remote_only or remote):
                out.append(p)
        if page + 1 >= int(data.get("nbPages") or 1):
            break
    return out[:MAX_POSTINGS_PER_FETCH]


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
                {
                    "name": "country",
                    "label": "Country code",
                    "default": "us",
                    "hint": "Two letters, like us or gb",
                },
                {"name": "distance_km", "label": "Distance in km (optional)", "type": "number"},
                {
                    "name": "max_days_old",
                    "label": "Max age in days",
                    "type": "number",
                    "default": 14,
                },
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
                    "default": "week",
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
            group="search",
            how_to="Many job sites and company career pages offer an RSS feed of new jobs; "
            "paste its link.",
        ),
        AdapterInfo(
            "workable",
            "Workable job board",
            "A company's public Workable careers page (apply.workable.com/<board>).",
            "https://help.workable.com/hc/en-us/articles/115012771647",
            BoardConfig,
            NoSecret,
            fetch_workable,
            _BOARD_FIELDS,
            how_to="Open the company's careers page. If the address is apply.workable.com/acme, "
            "the board name is acme.",
        ),
        AdapterInfo(
            "smartrecruiters",
            "SmartRecruiters job board",
            "A company's public SmartRecruiters postings (jobs.smartrecruiters.com/<company>).",
            "https://developers.smartrecruiters.com/docs/posting-api",
            BoardConfig,
            NoSecret,
            fetch_smartrecruiters,
            _BOARD_FIELDS,
            how_to="Open a job on the company's careers site. If the address is "
            "jobs.smartrecruiters.com/AcmeInc/..., the board name is AcmeInc (case matters).",
        ),
        AdapterInfo(
            "recruitee",
            "Recruitee job board",
            "A company's public Recruitee careers site (<company>.recruitee.com).",
            "https://docs.recruitee.com/reference/offers",
            BoardConfig,
            NoSecret,
            fetch_recruitee,
            _BOARD_FIELDS,
            how_to="If the company's jobs are at acme.recruitee.com (or link there), the board "
            "name is acme.",
        ),
        AdapterInfo(
            "personio",
            "Personio job board",
            "A company's public Personio jobs page (<company>.jobs.personio.de), common in Europe.",
            "https://developer.personio.de/docs/retrieving-open-job-positions",
            BoardConfig,
            NoSecret,
            fetch_personio,
            _BOARD_FIELDS,
            how_to="If the jobs page is acme.jobs.personio.de, the board name is acme.",
        ),
        AdapterInfo(
            "remotive",
            "Remotive (remote jobs)",
            "Remote jobs from Remotive's public API, filtered by your keywords. Remotive asks "
            "to be checked at most four times a day, and shows jobs a day late.",
            "https://remotive.com/api-documentation",
            KeywordConfig,
            NoSecret,
            fetch_remotive,
            [
                {
                    "name": "keywords",
                    "label": "Keywords",
                    "hint": "Comma-separated, like python, data analyst",
                },
                {
                    "name": "location",
                    "label": "Location contains (optional)",
                    "hint": "Like USA or Europe",
                },
            ],
            group="remote",
            min_interval_hours=6,
        ),
        AdapterInfo(
            "remoteok",
            "Remote OK (remote jobs)",
            "Remote jobs from Remote OK's public feed, filtered by your keywords.",
            "https://remoteok.com/api",
            KeywordConfig,
            NoSecret,
            fetch_remoteok,
            [
                {"name": "keywords", "label": "Keywords", "hint": "Comma-separated"},
                {"name": "location", "label": "Location contains (optional)"},
            ],
            group="remote",
            min_interval_hours=6,
        ),
        AdapterInfo(
            "himalayas",
            "Himalayas (remote jobs)",
            "Remote jobs from Himalayas' public API, searched by your keywords.",
            "https://himalayas.app/api",
            KeywordConfig,
            NoSecret,
            fetch_himalayas,
            [
                {
                    "name": "keywords",
                    "label": "Keywords",
                    "hint": "Comma-separated; up to five are searched",
                },
                {
                    "name": "location",
                    "label": "Location contains (optional)",
                    "hint": "Like United States",
                },
            ],
            group="remote",
            min_interval_hours=6,
        ),
        AdapterInfo(
            "jobicy",
            "Jobicy (remote jobs)",
            "Remote jobs from Jobicy's public API, searched by your keywords.",
            "https://jobicy.com/jobs-rss-feed",
            KeywordConfig,
            NoSecret,
            fetch_jobicy,
            [
                {
                    "name": "keywords",
                    "label": "Keywords",
                    "hint": "Comma-separated; up to five are searched",
                },
                {
                    "name": "location",
                    "label": "Region (optional)",
                    "hint": "Like usa, canada, uk, europe",
                },
            ],
            group="remote",
            min_interval_hours=6,
        ),
        AdapterInfo(
            "arbeitnow",
            "Arbeitnow (Europe)",
            "Jobs in Germany and Europe from Arbeitnow's public API, filtered by your keywords.",
            "https://www.arbeitnow.com/blog/job-board-api",
            KeywordConfig,
            NoSecret,
            fetch_arbeitnow,
            [
                {"name": "keywords", "label": "Keywords", "hint": "Comma-separated"},
                {"name": "location", "label": "City contains (optional)", "hint": "Like Berlin"},
            ],
            group="search",
            min_interval_hours=6,
        ),
        AdapterInfo(
            "hn_hiring",
            "Hacker News: Who is hiring?",
            'This month\'s "Ask HN: Who is hiring?" thread: hundreds of tech jobs posted '
            "directly by founders and hiring managers, filtered by your keywords.",
            "https://github.com/algolia/hn-search",
            HnConfig,
            NoSecret,
            fetch_hn_hiring,
            [
                {
                    "name": "keywords",
                    "label": "Keywords",
                    "required": True,
                    "hint": "Comma-separated, like python, data",
                },
                {"name": "remote_only", "label": "Remote only", "type": "checkbox"},
            ],
            group="community",
            min_interval_hours=12,
        ),
    ]
}

for _kind in ("adzuna", "usajobs", "jsearch"):
    ADAPTERS[_kind] = replace(ADAPTERS[_kind], group="search")
ADAPTERS["email_alerts"] = AdapterInfo(
    "email_alerts",
    "Job alert emails (LinkedIn, Indeed, and more)",
    "Reads the job-alert emails LinkedIn, Indeed, Glassdoor, ZipRecruiter, and Monster send "
    "you, and adds every job in them. CareerLine never visits those sites; it only reads the "
    "alert emails, read-only (nothing is marked read, moved, or deleted).",
    "https://www.rfc-editor.org/rfc/rfc9051",
    email_alerts.EmailAlertsConfig,
    email_alerts.EmailAlertsSecret,
    email_alerts.fetch_email_alerts,
    [
        {
            "name": "host",
            "label": "Mail server (IMAP)",
            "required": True,
            "default": "imap.gmail.com",
            "hint": "Gmail: imap.gmail.com · Outlook: outlook.office365.com · iCloud: "
            "imap.mail.me.com · Yahoo: imap.mail.yahoo.com",
        },
        {"name": "port", "label": "Port", "type": "number", "default": 993, "hint": "Usually 993"},
        {
            "name": "folder",
            "label": "Folder",
            "default": "INBOX",
            "hint": "INBOX, or a label/folder your alerts go to",
        },
        {"name": "days", "label": "Look back (days)", "type": "number", "default": 7},
        {
            "name": "sites",
            "label": "Alert emails from",
            "type": "multiselect",
            "options": list(email_alerts.SITES),
        },
    ],
    [
        {"name": "username", "label": "Email address", "required": True},
        {
            "name": "password",
            "label": "App password",
            "required": True,
            "secret": True,
            "hint": "Not your normal password: create an app password in your email account's "
            "security settings.",
        },
    ],
    group="inbox",
    min_interval_hours=2,
    how_to="1. On LinkedIn (and Indeed, etc.), save a job search and turn on email alerts "
    "(daily). 2. Optional: make a Gmail filter that labels them 'Job alerts' and use that as "
    "the folder. 3. Create an app password (Gmail: myaccount.google.com/apppasswords, needs "
    "2-step verification; Outlook: account.microsoft.com/security). 4. Enter it here. A "
    "separate mailbox just for alerts is the most private option.",
)
ADAPTERS["adzuna"] = replace(
    ADAPTERS["adzuna"],
    how_to="Get a free app ID and key at developer.adzuna.com (sign up, then Dashboard → API "
    "access details).",
)
ADAPTERS["usajobs"] = replace(
    ADAPTERS["usajobs"],
    how_to="Request a free key at developer.usajobs.gov/apirequest; it's emailed to you.",
)
ADAPTERS["jsearch"] = replace(
    ADAPTERS["jsearch"],
    description="Search Google for Jobs, which collects postings from LinkedIn, Indeed, "
    "Glassdoor, ZipRecruiter and company sites, through the JSearch API. Needs a RapidAPI "
    "key (free tier available).",
    how_to="Sign up at rapidapi.com, subscribe to JSearch (the free plan works), and copy "
    "your X-RapidAPI-Key.",
)
for _kind in ("greenhouse", "lever", "ashby"):
    host = {
        "greenhouse": "boards.greenhouse.io/acme (or job-boards.greenhouse.io/acme)",
        "lever": "jobs.lever.co/acme",
        "ashby": "jobs.ashbyhq.com/acme",
    }[_kind]
    ADAPTERS[_kind] = replace(
        ADAPTERS[_kind],
        how_to=f"Open the company's jobs page. If the address is {host}, the board name is acme.",
    )


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
