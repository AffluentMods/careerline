# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import httpx
import pytest
from fake_web import JOB_PAGE, FakeWeb

from careerline.discovery.adapters import SourceError, parse_feed, run_adapter
from careerline.discovery.normalize import (
    NormalizedPosting,
    detect_clearance,
    detect_shifts,
    detect_work_mode,
    fingerprint,
    html_to_text,
    parse_pay,
)
from careerline.discovery.page_import import (
    ImportRefused,
    blocked_domain,
    import_url,
    parse_job_page,
)
from careerline.net import FetchError, SafeFetcher, is_public_address, robots_allows

# --- SSRF guard -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.5",
        "192.168.1.10",
        "172.16.0.1",
        "169.254.169.254",
        "::1",
        "fc00::1",
        "0.0.0.0",
        "100.64.0.1",
        "::ffff:127.0.0.1",
        "224.0.0.1",
        # NAT64 (RFC 6052) and IPv4-compatible forms that embed private IPv4 addresses
        "64:ff9b::7f00:1",
        "64:ff9b::a00:1",
        "64:ff9b::a9fe:a9fe",
        "64:ff9b:1::a00:1",
        "::127.0.0.1",
        "::8.8.8.8",
    ],
)
def test_private_addresses_are_not_public(address: str) -> None:
    assert not is_public_address(address)


def test_public_addresses() -> None:
    assert is_public_address("8.8.8.8") and is_public_address("2606:4700:4700::1111")
    assert is_public_address("64:ff9b::808:808")  # NAT64 for 8.8.8.8


async def test_fetcher_blocks_private_targets() -> None:
    web = FakeWeb(
        addresses={"internal.example": ["10.1.2.3"], "split.example": ["8.8.8.8", "127.0.0.1"]}
    )
    f = web.fetcher()
    for url in (
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://internal.example/",
        "http://split.example/",  # one bad answer is enough to refuse
        "http://[::1]/",
    ):
        with pytest.raises(FetchError) as exc:
            await f.get(url)
        assert exc.value.code == "blocked_address", url
    assert web.requests == []


async def test_fetcher_rejects_odd_urls() -> None:
    f = FakeWeb().fetcher()
    for url in (
        "file:///etc/passwd",
        "ftp://example.org/",
        "https://user:pw@example.org/",
        "https://example.org:8443/",
    ):
        with pytest.raises(FetchError):
            await f.get(url)


async def test_fetcher_pins_the_checked_address_and_keeps_host() -> None:
    web = FakeWeb()
    await web.fetcher().get("https://feeds.example.org/jobs.rss")
    req = web.requests[-1]
    assert req.url.host == "93.184.216.34"
    assert req.headers["host"] == "feeds.example.org"
    assert req.extensions.get("sni_hostname") == "feeds.example.org"
    assert req.headers["user-agent"].startswith("CareerLine/")


async def test_redirect_to_private_address_is_blocked() -> None:
    web = FakeWeb(
        routes={"good.example/start": (302, "", {"location": "http://evil.example/next"})},
        addresses={"evil.example": ["192.168.0.1"]},
    )
    with pytest.raises(FetchError) as exc:
        await web.fetcher().get("http://good.example/start")
    assert exc.value.code == "blocked_address"


async def test_response_size_limit() -> None:
    web = FakeWeb(routes={"big.example/": (200, "x" * 2000, {})})
    with pytest.raises(FetchError) as exc:
        await web.fetcher(max_bytes=1000).get("https://big.example/")
    assert exc.value.code == "too_large"


async def test_robots_rules() -> None:
    web = FakeWeb()
    f = web.fetcher()
    assert await robots_allows(f, "https://careers.example.com/jobs/ir")
    assert not await robots_allows(f, "https://careers.example.com/private/x")
    assert await robots_allows(f, "https://feeds.example.org/jobs.rss")  # 404 robots = allowed
    web.routes["down.example/robots.txt"] = (503, "", {})
    assert not await robots_allows(f, "https://down.example/page")  # 5xx = disallowed


# --- normalization -------------------------------------------------------------------------------


def test_html_to_text_strips_markup_and_scripts() -> None:
    text = html_to_text(
        "&lt;p&gt;Hi&lt;/p&gt;&lt;script&gt;alert(1)&lt;/script&gt;&lt;ul&gt;&lt;li&gt;One&lt;/li&gt;&lt;/ul&gt;"
    )
    assert text == "Hi\n\n• One"
    assert "alert" not in text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Pay: $50 - $65 per hour", (5000, 6500, "hour")),
        ("$110,000 – $130,000", (11_000_000, 13_000_000, "year")),
        ("$95k-$120k a year", (9_500_000, 12_000_000, "year")),
        ("Starting at $22.50/hr", (2250, 2250, "hour")),
        ("Benefits include 401k", None),
    ],
)
def test_parse_pay(text: str, expected: tuple[int, int, str] | None) -> None:
    assert parse_pay(text) == expected


def test_detectors() -> None:
    assert detect_work_mode("This is a hybrid role") == "hybrid"
    assert detect_work_mode("Location: Remote (US)") == "remote"
    assert detect_work_mode("Must work on-site") == "onsite"
    assert detect_work_mode("Springfield office") is None
    assert detect_shifts("Early shift 6am - 2:30pm, some weekends required") == ["early", "weekend"]
    assert detect_shifts("Overnight monitoring, rotating shifts") == ["night", "rotating"]
    assert detect_clearance("Active TS/SCI required") == "top_secret"
    assert detect_clearance("Public Trust position") == "public_trust"
    assert detect_clearance("Must be able to obtain a clearance") == "secret"
    assert detect_clearance("No clearance needed") is None


def test_fingerprint_ignores_noise() -> None:
    a = fingerprint("Northwind Tools, Inc.", "Security Engineer", "Springfield, ST")
    b = fingerprint("northwind tools", "Security  Engineer", "Springfield")
    assert a == b
    assert fingerprint("X", "Engineer", "Remote - US") == fingerprint(
        "X", "Engineer", "Anywhere (remote)"
    )
    assert a != fingerprint("Northwind Tools", "Security Engineer II", "Springfield, ST")


def test_finish_clips_and_detects() -> None:
    p = NormalizedPosting(
        external_id="1",
        title="  ",
        url="javascript:alert(1)",
        description="Remote role, $40-$50/hr",
    ).finish()
    assert p.title == "Untitled job" and p.url == ""
    assert p.work_mode == "remote" and (p.pay_min_cents, p.pay_max_cents, p.pay_period) == (
        4000,
        5000,
        "hour",
    )


# --- adapters ----------------------------------------------------------------------------------


async def test_greenhouse() -> None:
    jobs = await run_adapter(
        FakeWeb().fetcher(), "greenhouse", {"board": "northwind", "company": "Northwind Tools"}, {}
    )
    assert [j.title for j in jobs] == ["Security Engineer", "Tier 1 SOC Analyst"]
    first = jobs[0]
    assert first.company == "Northwind Tools" and first.location == "Springfield, ST"
    assert (first.pay_min_cents, first.pay_max_cents, first.pay_period) == (5000, 6500, "hour")
    assert first.work_mode == "hybrid" and "day" in first.shifts
    assert "• Build detections in Python" in first.description
    assert jobs[1].clearance == "secret" and jobs[1].shifts == ["night"]


async def test_lever_ashby_jsearch_usajobs() -> None:
    web = FakeWeb()
    lever = await run_adapter(web.fetcher(), "lever", {"board": "northwind"}, {})
    assert lever[0].pay_period == "year" and lever[0].pay_min_cents == 10_400_000
    assert lever[0].work_mode == "hybrid"

    ashby = await run_adapter(web.fetcher(), "ashby", {"board": "tailspin"}, {})
    assert [a.title for a in ashby] == ["Detection Engineer"]  # unlisted job skipped
    assert ashby[0].work_mode == "remote" and ashby[0].pay_max_cents == 14_000_000
    assert "early" in ashby[0].shifts

    js = await run_adapter(web.fetcher(), "jsearch", {"query": "security"}, {"api_key": "k"})
    assert js[0].pay_period == "hour" and js[0].work_mode == "remote"
    assert web.requests[-1].headers["x-rapidapi-key"] == "k"

    usa = await run_adapter(
        web.fetcher(),
        "usajobs",
        {"keyword": "security"},
        {"email": "jordan@example.com", "api_key": "k"},
    )
    assert usa[0].clearance == "public_trust" and usa[0].pay_min_cents == 8_800_000
    assert str(usa[0].closes_on) == "2026-11-26"
    assert web.requests[-1].headers["user-agent"] == "jordan@example.com"
    assert web.requests[-1].headers["authorization-key"] == "k"


async def test_adzuna_drops_predicted_salaries() -> None:
    web = FakeWeb()
    jobs = await run_adapter(
        web.fetcher(), "adzuna", {"what": "network"}, {"app_id": "a", "app_key": "b"}
    )
    assert jobs[0].title == "Network Engineer"
    assert jobs[0].pay_min_cents is None and jobs[0].lat == 39.8
    assert web.requests[-1].url.params["app_key"] == "b"


async def test_rss() -> None:
    jobs = await run_adapter(
        FakeWeb().fetcher(), "rss", {"url": "https://feeds.example.org/jobs.rss"}, {}
    )
    assert jobs[0].title == "Help Desk Technician" and jobs[0].external_id == "job-1"
    assert jobs[0].pay_min_cents == 2200 and jobs[0].posted_at is not None


def test_feed_parser_refuses_entity_bombs() -> None:
    bomb = b"""<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;">]>
    <rss><channel><item><title>&lol2;</title></item></channel></rss>"""
    with pytest.raises(SourceError):
        parse_feed(bomb)


async def test_adapter_errors_are_friendly() -> None:
    web = FakeWeb(routes={"jsearch.p.rapidapi.com/search": (403, {"message": "bad key"}, {})})
    with pytest.raises(SourceError, match="rejected the credentials"):
        await run_adapter(web.fetcher(), "jsearch", {"query": "x"}, {"api_key": "nope"})
    with pytest.raises(SourceError, match="settings are incomplete"):
        await run_adapter(web.fetcher(), "adzuna", {"what": "x"}, {})
    with pytest.raises(SourceError, match="couldn't find"):
        await run_adapter(web.fetcher(), "greenhouse", {"board": "missing"}, {})


# --- URL import ----------------------------------------------------------------------------------


def test_json_ld_job_page() -> None:
    p = parse_job_page(JOB_PAGE, "https://careers.example.com/jobs/ir")
    assert p.title == "Incident Responder" and p.company == "Litware Inc"
    assert p.location == "Springfield, ST"
    assert (p.pay_min_cents, p.pay_max_cents, p.pay_period) == (5500, 7000, "hour")
    assert "alert" not in p.description and "Ignore previous instructions." in p.description
    assert str(p.closes_on) == "2026-12-01"


async def test_import_url_rules() -> None:
    web = FakeWeb()
    f = web.fetcher()
    p = await import_url(f, "https://careers.example.com/jobs/ir")
    assert p.title == "Incident Responder"
    with pytest.raises(ImportRefused) as exc:
        await import_url(f, "https://careers.example.com/private/x")
    assert exc.value.code == "robots_disallowed"
    with pytest.raises(ImportRefused) as exc:
        await import_url(f, "https://www.linkedin.com/jobs/view/123")
    assert exc.value.code == "site_not_allowed"
    assert blocked_domain("https://uk.indeed.com/x") == "indeed.com"
    assert blocked_domain("https://notindeed.com/x") is None
    assert len([r for r in web.requests if "linkedin" in r.headers["host"]]) == 0


async def test_real_transport_never_used_in_tests() -> None:
    # Sanity check for the test harness: the fake fetcher uses a MockTransport.
    f = FakeWeb().fetcher()
    assert isinstance(f.transport, httpx.MockTransport)
    assert isinstance(f, SafeFetcher)
