# SPDX-License-Identifier: Apache-2.0
"""The added job sources: company boards, remote boards, HN, and job-alert emails.

All data is fictional, shaped like each provider's real responses (checked November 2026).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime

import httpx
import pytest

from careerline.discovery import email_alerts
from careerline.discovery.adapters import ADAPTERS, SourceError, run_adapter
from tests.fake_web import FakeWeb

WORKABLE = {
    "name": "Tailspin Toys",
    "jobs": [
        {
            "title": "Data Analyst",
            "shortcode": "AB12CD34EF",
            "employment_type": "Full-time",
            "telecommuting": True,
            "url": "https://apply.workable.com/j/AB12CD34EF",
            "published_on": "2026-11-01",
            "city": "Springfield",
            "state": "ST",
            "country": "United States",
            "description": "<p>SQL and Python. Build dashboards.</p>",
        }
    ],
}
SMART_LIST = {
    "content": [
        {
            "id": "7440001",
            "name": "Security Analyst",
            "company": {"name": "Contoso"},
            "releasedDate": "2026-11-10T10:00:00.000Z",
            "location": {
                "fullLocation": "Riverton, , United States",
                "remote": False,
                "hybrid": True,
            },
            "typeOfEmployment": {"label": "Full-time"},
        }
    ]
}
SMART_DETAIL = {
    "jobAd": {
        "sections": {
            "jobDescription": {"text": "<p>Investigate alerts in Splunk.</p>"},
            "qualifications": {"text": "<ul><li>2 years in a SOC</li></ul>"},
        }
    }
}
RECRUITEE = {
    "offers": [
        {
            "id": 2820001,
            "title": "Platform Engineer",
            "status": "published",
            "company_name": "Fabrikam",
            "location": "Amsterdam, Netherlands",
            "remote": False,
            "hybrid": True,
            "employment_type_code": "fulltime_permanent",
            "salary": {"min": "60000", "max": "80000", "period": "year", "currency": "EUR"},
            "description": "<p>Kubernetes and Go.</p>",
            "requirements": "<ul><li>Terraform</li></ul>",
            "careers_url": "https://careers.fabrikam.example/o/platform-engineer",
            "published_at": "2026-11-11 13:21:40 UTC",
        }
    ]
}
PERSONIO = b"""<?xml version="1.0" encoding="UTF-8"?>
<workzag-jobs><position><id>900001</id><office>Berlin</office><name>Backend Developer</name>
<jobDescriptions><jobDescription><name>Your tasks</name><value>&lt;p&gt;Python APIs&lt;/p&gt;</value>
</jobDescription></jobDescriptions><schedule>full-time</schedule>
<createdAt>2026-11-11T17:43:42+00:00</createdAt></position></workzag-jobs>"""
REMOTIVE = {
    "0-legal-notice": "...",
    "jobs": [
        {
            "id": 501,
            "url": "https://remotive.com/remote-jobs/software-dev/python-dev-501",
            "title": "Python Developer",
            "company_name": "Litware",
            "job_type": "full_time",
            "publication_date": "2026-11-13T17:29:57",
            "candidate_required_location": "USA",
            "salary": "$85k - $155k",
            "description": "<p>Django and PostgreSQL.</p>",
        },
        {
            "id": 502,
            "url": "https://remotive.com/remote-jobs/marketing/x-502",
            "title": "Content Creator",
            "company_name": "Adatum",
            "candidate_required_location": "Worldwide",
            "description": "<p>Video.</p>",
        },
    ],
}
REMOTEOK = [
    {"legal": "Please link back to Remote OK."},
    {
        "id": "1137792",
        "position": "Research Engineer",
        "company": "Proseware",
        "date": "2026-11-12T16:00:38+00:00",
        "location": "",
        "salary_min": 120000,
        "salary_max": 160000,
        "description": "<p>Python and privacy research.</p>",
        "url": "https://remoteOK.com/remote-jobs/remote-research-engineer-1137792",
    },
]
ARBEITNOW = {
    "data": [
        {
            "slug": "backend-berlin-1",
            "company_name": "Northwind",
            "title": "Backend Engineer",
            "description": "<p>Python services.</p>",
            "remote": False,
            "url": "https://www.arbeitnow.com/jobs/companies/northwind/backend-berlin-1",
            "job_types": ["full time"],
            "location": "Berlin",
            "created_at": 1794783635,
        },
        {
            "slug": "sales-munich-2",
            "company_name": "Contoso",
            "title": "Sales Manager",
            "description": "<p>Sales.</p>",
            "url": "https://www.arbeitnow.com/jobs/companies/contoso/sales-munich-2",
            "location": "Munich",
            "created_at": 1794783635,
        },
    ],
    "links": {"next": None},
}
HIMALAYAS = {
    "jobs": [
        {
            "title": "Senior Python Engineer",
            "companyName": "Tailspin",
            "employmentType": "Full Time",
            "minSalary": 150000,
            "maxSalary": 180000,
            "salaryPeriod": "annual",
            "currency": "USD",
            "locationRestrictions": ["United States"],
            "description": "<p>Python.</p>",
            "pubDate": 1794762765,
            "applicationLink": "https://himalayas.app/companies/tailspin/jobs/senior-python",
            "guid": "https://himalayas.app/companies/tailspin/jobs/senior-python",
        }
    ]
}
JOBICY = {
    "jobs": [
        {
            "id": 162483,
            "url": "https://jobicy.com/jobs/162483-platform-architect",
            "jobTitle": "Platform Architect",
            "companyName": "Wingtip",
            "jobType": ["Full-Time"],
            "jobGeo": "USA",
            "jobDescription": "<p>Kubernetes.</p>",
            "pubDate": "2026-11-15T13:37:59+00:00",
            "salaryMin": 140000,
            "salaryMax": 170000,
            "salaryCurrency": "USD",
            "salaryPeriod": "yearly",
        }
    ]
}
HN_STORIES = {
    "hits": [
        {"title": "Ask HN: Who wants to be hired? (November 2026)", "objectID": "100"},
        {"title": "Ask HN: Who is hiring? (November 2026)", "objectID": "200"},
    ]
}
HN_COMMENTS = {
    "nbPages": 1,
    "hits": [
        {
            "objectID": "201",
            "parent_id": 200,
            "created_at": "2026-11-02T15:00:00Z",
            "comment_text": "Fabrikam | Senior Python Engineer | Remote (US) | Full-time | $160k<p>"
            "We build data tools. Email jobs@fabrikam.example",
        },
        {
            "objectID": "202",
            "parent_id": 200,
            "created_at": "2026-11-02T15:05:00Z",
            "comment_text": "Contoso | iOS Developer | Seattle, WA | Onsite<p>Swift.",
        },
        {
            "objectID": "203",
            "parent_id": 201,
            "comment_text": "Is this still open? I love python",
        },
        {"objectID": "204", "parent_id": 200, "comment_text": "Great thread, thanks!"},
    ],
}


def _web() -> FakeWeb:
    web = FakeWeb()
    web.routes.update(
        {
            "apply.workable.com/api/v1/widget/accounts/tailspin": (200, WORKABLE, {}),
            "api.smartrecruiters.com/v1/companies/Contoso/postings": (200, SMART_LIST, {}),
            "api.smartrecruiters.com/v1/companies/Contoso/postings/7440001": (
                200,
                SMART_DETAIL,
                {},
            ),
            "fabrikam.recruitee.com/api/offers/": (200, RECRUITEE, {}),
            "northwind.jobs.personio.de/xml": (200, PERSONIO, {"content-type": "text/xml"}),
            "remotive.com/api/remote-jobs": (200, REMOTIVE, {}),
            "remoteok.com/api": (200, REMOTEOK, {}),
            "www.arbeitnow.com/api/job-board-api": (200, ARBEITNOW, {}),
            "himalayas.app/jobs/api/search": (200, HIMALAYAS, {}),
            "jobicy.com/api/v2/remote-jobs": (200, JOBICY, {}),
            "hn.algolia.com/api/v1/search_by_date": (200, HN_STORIES, {}),
            "hn.algolia.com/api/v1/search": (200, HN_COMMENTS, {}),
        }
    )
    return web


async def test_company_boards() -> None:
    web = _web()
    [w] = await run_adapter(web.fetcher(), "workable", {"board": "tailspin"}, {})
    assert (w.title, w.company, w.work_mode) == ("Data Analyst", "Tailspin Toys", "remote")
    assert w.location == "Springfield, ST, United States" and "SQL and Python" in w.description

    [s] = await run_adapter(web.fetcher(), "smartrecruiters", {"board": "Contoso"}, {})
    assert s.company == "Contoso" and s.work_mode == "hybrid"
    assert "Splunk" in s.description and "2 years in a SOC" in s.description
    assert s.url == "https://jobs.smartrecruiters.com/Contoso/7440001"

    [r] = await run_adapter(web.fetcher(), "recruitee", {"board": "fabrikam"}, {})
    assert (r.pay_min_cents, r.pay_max_cents, r.currency) == (6_000_000, 8_000_000, "EUR")
    assert "Terraform" in r.description and r.work_mode == "hybrid"

    [p] = await run_adapter(web.fetcher(), "personio", {"board": "northwind"}, {})
    assert (p.title, p.location) == ("Backend Developer", "Berlin")
    assert "Python APIs" in p.description and p.posted_at is not None


async def test_remote_boards_filter_by_keywords() -> None:
    web = _web()
    remotive = await run_adapter(web.fetcher(), "remotive", {"keywords": "python, django"}, {})
    assert [j.title for j in remotive] == ["Python Developer"]
    assert (remotive[0].pay_min_cents, remotive[0].pay_max_cents) == (8_500_000, 15_500_000)
    assert "Source: Remotive" in remotive[0].description

    ok = await run_adapter(web.fetcher(), "remoteok", {"keywords": "python"}, {})
    assert [j.company for j in ok] == ["Proseware"] and ok[0].location == "Remote"
    assert await run_adapter(web.fetcher(), "remoteok", {"keywords": "cobol"}, {}) == []

    arb = await run_adapter(
        web.fetcher(), "arbeitnow", {"keywords": "python", "location": "berlin"}, {}
    )
    assert [j.title for j in arb] == ["Backend Engineer"]

    him = await run_adapter(web.fetcher(), "himalayas", {"keywords": "python"}, {})
    assert him[0].pay_min_cents == 15_000_000 and him[0].location == "United States"

    job = await run_adapter(
        web.fetcher(), "jobicy", {"keywords": "kubernetes", "location": "usa"}, {}
    )
    assert job[0].title == "Platform Architect"
    sent = next(r for r in web.requests if r.headers["host"] == "jobicy.com")
    assert sent.url.params["tag"] == "kubernetes" and sent.url.params["geo"] == "usa"


async def test_hn_who_is_hiring() -> None:
    web = _web()
    jobs = await run_adapter(web.fetcher(), "hn_hiring", {"keywords": "python"}, {})
    # Only top-level posts in the right thread, in "Company | Role | ..." form, that match.
    assert [(j.company, j.title, j.location, j.work_mode) for j in jobs] == [
        ("Fabrikam", "Senior Python Engineer", "Remote (US)", "remote")
    ]
    assert jobs[0].url == "https://news.ycombinator.com/item?id=201"
    search = next(r for r in web.requests if r.url.path == "/api/v1/search")
    assert search.url.params["tags"] == "comment,story_200"
    remote = await run_adapter(
        web.fetcher(), "hn_hiring", {"keywords": "swift", "remote_only": True}, {}
    )
    assert remote == []


# --- job alert emails -------------------------------------------------------------------------


def _mail(sender: str, html: str, plain: str = "") -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "me@example.com"
    msg["Subject"] = "New jobs for you"
    msg["Date"] = format_datetime(datetime(2026, 11, 14, 9, 0, tzinfo=UTC))
    msg.set_content(plain or "Open this email in HTML.")
    msg.add_alternative(html, subtype="html")
    return msg.as_bytes()


LINKEDIN_HTML = """<html><head><style>.x{}</style></head><body>
<table><tr><td><a href="https://www.linkedin.com/comm/jobs/view/4012345678/?trackingId=abc&amp;refId=x">
Security Engineer</a></td></tr>
<tr><td>Northwind Tools · Riverton, WA (Hybrid)</td></tr>
<tr><td>Actively recruiting</td></tr>
<tr><td><a href="https://www.linkedin.com/comm/jobs/view/4012345678/?trackingId=abc">View job</a></td></tr>
<tr><td><a href="https://www.linkedin.com/comm/jobs/view/4099999999/?trk=eml">
Detection Engineer</a></td></tr>
<tr><td>Contoso</td></tr><tr><td>Remote</td></tr>
<tr><td><a href="https://www.linkedin.com/comm/jobs/search/?keywords=security">See all jobs</a></td></tr>
<tr><td><a href="https://www.linkedin.com/comm/psettings/email-unsubscribe">Unsubscribe</a></td></tr>
</table></body></html>"""

INDEED_HTML = """<html><body>
<a href="https://click.indeed.example/?u=https%3A%2F%2Fwww.indeed.com%2Frc%2Fclk%2Fdl%3Fjk%3D1a2b3c4d5e6f7a8b%26from%3Dja">
<b>Data Analyst</b></a><br>Fabrikam - Springfield, ST<br>$70,000 - $90,000 a year
<a href="https://www.indeed.com/viewjob?jk=9f8e7d6c5b4a3210&amp;from=ja">SOC Analyst I</a>
<div>Adatum Corp - Remote</div>
<a href="https://www.indeed.com/unsubscribe?id=1">Unsubscribe</a>
</body></html>"""


def test_parse_linkedin_alert() -> None:
    jobs = email_alerts.parse_alert(
        _mail("LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>", LINKEDIN_HTML),
        list(email_alerts.SITES),
    )
    assert [(j.title, j.company, j.location) for j in jobs] == [
        ("Security Engineer", "Northwind Tools", "Riverton, WA (Hybrid)"),
        ("Detection Engineer", "Contoso", "Remote"),
    ]
    # Tracking parameters are dropped from the links kept.
    assert jobs[0].url == "https://www.linkedin.com/jobs/view/4012345678/"
    assert jobs[0].external_id == "linkedin:4012345678"
    assert "LinkedIn job alert" in jobs[0].description
    assert jobs[0].posted_at == datetime(2026, 11, 14, 9, 0, tzinfo=UTC)


def test_parse_indeed_alert_and_ignore_other_senders() -> None:
    raw = _mail("Indeed <alert@indeed.com>", INDEED_HTML)
    jobs = email_alerts.parse_alert(raw, ["indeed"])
    assert [(j.title, j.company, j.location) for j in jobs] == [
        ("Data Analyst", "Fabrikam", "Springfield, ST"),
        ("SOC Analyst I", "Adatum Corp", "Remote"),
    ]
    assert jobs[0].url == "https://www.indeed.com/viewjob?jk=1a2b3c4d5e6f7a8b"
    # Not chosen as a site, or not from a job site at all: nothing.
    assert email_alerts.parse_alert(raw, ["linkedin"]) == []
    for sender in (
        "Totally LinkedIn <jobs@linkedln-careers.example>",
        "LinkedIn <jobalerts@linkedin.com.evil.example>",
        '"jobalerts@linkedin.com" <x@evil.example>',
    ):
        assert (
            email_alerts.parse_alert(_mail(sender, LINKEDIN_HTML), list(email_alerts.SITES)) == []
        )
    sub = _mail("LinkedIn <jobs-noreply@e.linkedin.com>", LINKEDIN_HTML)
    assert len(email_alerts.parse_alert(sub, ["linkedin"])) == 2


@pytest.fixture
def mailbox() -> Iterator[list[bytes]]:
    messages: list[bytes] = []
    calls: list[tuple[str, int, str]] = []

    def reader(host, port, address, cfg, sec):  # type: ignore[no-untyped-def]
        calls.append((host, port, address))
        assert sec.password == "app-pass"
        return list(messages)

    email_alerts.set_reader(reader)
    messages.append(_mail("jobalerts-noreply@linkedin.com", LINKEDIN_HTML))
    messages.append(_mail("jobalerts-noreply@linkedin.com", LINKEDIN_HTML))  # a repeat
    messages.append(_mail("alert@indeed.com", INDEED_HTML))
    yield messages
    email_alerts.set_reader(None)


async def test_email_alert_source(mailbox: list[bytes]) -> None:
    web = FakeWeb(addresses={"imap.mail.example": ["93.184.216.34"], "mail.lan": ["10.0.0.5"]})
    secret = {"username": "me@example.com", "password": "app-pass"}
    jobs = await run_adapter(web.fetcher(), "email_alerts", {"host": "imap.mail.example"}, secret)
    assert sorted(j.external_id for j in jobs) == [
        "indeed:1a2b3c4d5e6f7a8b",
        "indeed:9f8e7d6c5b4a3210",
        "linkedin:4012345678",
        "linkedin:4099999999",
    ]
    with pytest.raises(SourceError, match="private network"):
        await run_adapter(web.fetcher(), "email_alerts", {"host": "mail.lan"}, secret)
    jobs = await run_adapter(
        web.fetcher(allow_private=True), "email_alerts", {"host": "mail.lan"}, secret
    )
    assert len(jobs) == 4
    with pytest.raises(SourceError, match="incomplete"):
        await run_adapter(web.fetcher(), "email_alerts", {"sites": ["myspace"]}, secret)


async def test_sources_api_lists_groups_and_enforces_intervals(
    user_client: httpx.AsyncClient,
) -> None:
    adapters = (await user_client.get("/api/v1/discovery/adapters")).json()
    by_kind = {a["kind"]: a for a in adapters}
    assert set(ADAPTERS) <= set(by_kind)
    assert by_kind["email_alerts"]["group"] == "inbox"
    assert by_kind["remotive"]["min_interval_hours"] == 6
    assert "acme" in by_kind["workable"]["how_to"]
    r = await user_client.post(
        "/api/v1/discovery/sources",
        json={
            "kind": "remotive",
            "name": "Remote Python",
            "interval_hours": 1,
            "enabled": False,
            "config": {"keywords": "python"},
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["interval_hours"] == 6
    sid = r.json()["id"]
    r = await user_client.patch(f"/api/v1/discovery/sources/{sid}", json={"interval_hours": 2})
    assert r.json()["interval_hours"] == 6
    r = await user_client.post(
        "/api/v1/discovery/sources",
        json={
            "kind": "email_alerts",
            "name": "Alerts",
            "enabled": False,
            "config": {"host": "imap.gmail.com", "sites": ["linkedin"]},
            "secret": {"username": "me@example.com", "password": "app-pass"},
        },
    )
    assert r.status_code == 201, r.text
    assert "app-pass" not in r.text
