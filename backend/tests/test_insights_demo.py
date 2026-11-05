# SPDX-License-Identifier: Apache-2.0
"""Insights (analytics) and demo mode."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import httpx
import pytest

from waypoint import db
from waypoint.config import Settings
from waypoint.demo import DEMO_EMAIL, DEMO_PASSWORD, seed_demo
from waypoint.insights.service import AppFacts, summarize
from waypoint.net import FetchError, OfflineFetcher
from waypoint.security.crypto import FieldCipher

TODAY = date(2026, 11, 4)


def _f(
    source: str, stage: str, applied: str | None, reached: set[str], first: str | None = None
) -> AppFacts:
    return AppFacts(
        source=source,
        stage=stage,
        applied_on=date.fromisoformat(applied) if applied else None,
        reached={stage, *reached},
        first_response=date.fromisoformat(first) if first else None,
    )


def test_summary_math() -> None:
    facts = [
        _f(
            "Referral",
            "offer",
            "2026-10-01",
            {"applied", "screening", "interviewing"},
            "2026-10-06",
        ),
        _f("Referral", "interviewing", "2026-10-10", {"applied"}, "2026-10-24"),
        _f("Job board", "rejected", "2026-10-01", {"applied"}, "2026-10-29"),
        _f("Job board", "applied", "2026-09-01", set()),  # quiet for 2 months
        _f("Job board", "withdrawn", "2026-10-20", {"applied"}),
        _f("", "saved", None, set()),
    ]
    out = summarize(facts, TODAY)
    totals = out["totals"]
    assert isinstance(totals, dict)
    assert totals["tracked"] == 6 and totals["applied"] == 5
    assert totals["responded"] == 3 and totals["positive"] == 2
    assert totals["interviews"] == 2 and totals["offers"] == 1
    assert totals["quiet"] == 1 and totals["withdrawn"] == 1
    assert out["response_rate"] == 0.6
    assert out["median_days_to_response"] == 14.0  # 5, 14, 28
    funnel = {s["stage"]: s for s in out["funnel"]}  # type: ignore[attr-defined]
    assert funnel["applied"]["count"] == 5
    assert funnel["screening"]["count"] == 2  # an interview implies getting past screening
    assert funnel["offer"]["rate_from_previous"] == 0.5
    sources = {s["source"]: s for s in out["sources"]}  # type: ignore[attr-defined]
    assert sources["Referral"]["response_rate"] == 1.0
    assert sources["Job board"]["response_rate"] == pytest.approx(0.333)
    assert "Not set" not in sources  # never applied, so not counted
    buckets = {b["bucket"]: b["count"] for b in out["response_times"]}  # type: ignore[attr-defined]
    assert sum(buckets.values()) == 3
    assert sum(w["applied"] for w in out["weekly"]) == 5  # type: ignore[attr-defined]


def test_empty_summary() -> None:
    out = summarize([], TODAY)
    assert out["response_rate"] is None and out["median_days_to_response"] is None


async def test_insights_api(two_users: tuple[httpx.AsyncClient, httpx.AsyncClient]) -> None:
    a, b = two_users
    T = "/api/v1/tracker"
    for source, stages in [
        ("Referral", ["applied", "screening", "interviewing"]),
        ("Job board", ["applied", "rejected"]),
        ("Job board", ["applied"]),
    ]:
        app = (
            await a.post(f"{T}/applications", json={"title": "Analyst", "source": source})
        ).json()
        for stage in stages:
            await a.post(f"{T}/applications/{app['id']}/stage", json={"stage": stage})
    r = await a.get("/api/v1/insights")
    assert r.status_code == 200
    data = r.json()
    assert data["totals"]["applied"] == 3 and data["totals"]["responded"] == 2
    assert data["median_days_to_response"] == 0.0
    assert [s["source"] for s in data["sources"]] == ["Job board", "Referral"]
    assert (await a.get("/api/v1/insights?days=0")).status_code == 422
    assert (await a.get("/api/v1/insights?days=30")).json()["totals"]["applied"] == 3
    # Another user's numbers are their own.
    assert (await b.get("/api/v1/insights")).json()["totals"]["tracked"] == 0


async def _demo_client(
    app_factory: Callable[[Settings], httpx.AsyncClient], s: Settings
) -> httpx.AsyncClient:
    c = app_factory(s)
    await c.get("/api/v1/auth/status")
    async with db.get_sessionmaker()() as session:
        assert (
            await seed_demo(session, FieldCipher(s.encryption_keys.get_secret_value())) is not None
        )
        await session.commit()
        assert await seed_demo(session, FieldCipher(s.encryption_keys.get_secret_value())) is None
    r = await c.post("/api/v1/auth/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD})
    assert r.status_code == 200, r.text
    return c


async def test_demo_mode(
    app_factory: Callable[[Settings], httpx.AsyncClient], make_settings: Callable[..., Settings]
) -> None:
    s = make_settings(demo_mode=True, allow_registration=True)
    c = await _demo_client(app_factory, s)
    status = (await c.get("/api/v1/auth/status")).json()
    assert status["demo"] is True and status["registration_open"] is False
    r = await c.post(
        "/api/v1/auth/register",
        json={"email": "x@example.com", "password": "a long password", "display_name": "X"},
    )
    assert r.status_code == 403

    # The seeded data is complete enough to show every page.
    summary = (await c.get("/api/v1/vault/summary")).json()
    assert summary["kinds"]["role"]["total"] == 2 and summary["stories"]["verified"] == 2
    assert summary["needs_review"] >= 1
    insights = (await c.get("/api/v1/insights")).json()
    assert insights["totals"]["applied"] == 9 and insights["totals"]["offers"] == 1
    assert insights["median_days_to_response"] is not None
    postings = (await c.get("/api/v1/discovery/postings?view=all")).json()
    assert len(postings["items"]) == 6
    assert all(p["score"] is not None for p in postings["items"])
    scam = next(p for p in postings["items"] if p["company"] == "QuickCash Rewards")
    best = max(p["score"]["score"] for p in postings["items"])
    assert scam["score"]["score"] < best
    agenda = (await c.get("/api/v1/tracker/agenda")).json()
    assert len(agenda) >= 3
    contacts = (await c.get("/api/v1/tracker/contacts")).json()
    assert {x["email"] for x in contacts} == {"jordan.avery@example.com", "sam.patel@example.com"}

    # Nothing leaves the server.
    r = await c.put("/api/v1/ai/settings", json={"provider": "anthropic", "api_key": "sk-x"})
    assert r.status_code == 403
    r = await c.post("/api/v1/discovery/import", json={"url": "https://example.com/job"})
    assert r.status_code == 403 and "demo mode" in r.text
    source = (await c.get("/api/v1/discovery/sources")).json()[0]
    r = await c.post(f"/api/v1/discovery/sources/{source['id']}/run")
    assert r.status_code == 403


async def test_offline_fetcher_refuses_everything() -> None:
    with pytest.raises(FetchError, match="demo mode"):
        await OfflineFetcher().get("https://example.com/")
