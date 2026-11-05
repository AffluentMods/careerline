# SPDX-License-Identifier: Apache-2.0
"""Discovery API + worker tests against a real database and a fake web."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
import pytest
from conftest import TEST_KEY
from fake_web import FakeWeb
from sqlalchemy import select, update

from waypoint import db as dbmod
from waypoint.config import Settings
from waypoint.discovery.models import JobSource, QueuedJob
from waypoint.security.crypto import FieldCipher
from waypoint.worker import queue
from waypoint.worker.runner import WorkerContext, drain, schedule_due

D = "/api/v1/discovery"
NORTHWIND_GH = {
    "kind": "greenhouse",
    "name": "Northwind (Greenhouse)",
    "config": {"board": "northwind", "company": "Northwind Tools"},
}
NORTHWIND_LEVER = {
    "kind": "lever",
    "name": "Northwind (Lever)",
    "config": {"board": "northwind", "company": "Northwind Tools"},
}


def use_web(client: httpx.AsyncClient, web: FakeWeb) -> None:
    client._transport.app.state.fetcher_factory = web.factory()  # type: ignore[attr-defined]


def worker(web: FakeWeb, settings: Settings) -> WorkerContext:
    return WorkerContext(
        sessionmaker=dbmod.get_sessionmaker(),
        settings=settings,
        cipher=FieldCipher(f"1:{TEST_KEY}"),
        fetcher_factory=web.factory(),
        worker_id="test",
    )


async def _source(c: httpx.AsyncClient, body: dict[str, Any]) -> Any:
    r = await c.post(f"{D}/sources", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def _postings(c: httpx.AsyncClient, view: str = "matches") -> Any:
    r = await c.get(f"{D}/postings", params={"view": view})
    assert r.status_code == 200, r.text
    return r.json()


async def test_requires_sign_in(client: httpx.AsyncClient) -> None:
    for path in ("/adapters", "/sources", "/postings", "/filters"):
        assert (await client.get(D + path)).status_code == 401


async def test_sources_fetch_dedupe_and_track(
    user_client: httpx.AsyncClient, settings: Settings
) -> None:
    c = user_client
    web = FakeWeb()
    kinds = [a["kind"] for a in (await c.get(f"{D}/adapters")).json()]
    assert kinds == ["greenhouse", "lever", "ashby", "adzuna", "usajobs", "jsearch", "rss"]

    gh = await _source(c, NORTHWIND_GH)
    assert gh["pending"] is True and gh["last_status"] == "never" and gh["has_secret"] is False
    await _source(c, NORTHWIND_LEVER)
    assert await drain(worker(web, settings)) >= 2

    sources = (await c.get(f"{D}/sources")).json()
    assert [(s["last_status"], s["last_found"], s["pending"]) for s in sources] == [
        ("ok", 2, False),
        ("ok", 1, False),
    ]
    assert sources[1]["last_new"] == 0  # the Lever job duplicates the Greenhouse one

    data = await _postings(c, "all")
    assert data["counts"]["all"] == 2
    eng = next(p for p in data["items"] if p["title"] == "Security Engineer")
    assert eng["sources"] == ["Northwind (Greenhouse)", "Northwind (Lever)"]
    assert eng["pay_period"] == "hour"  # kept from the first sighting, not overwritten

    detail = (await c.get(f"{D}/postings/{eng['id']}")).json()
    assert "Build detections in Python" in detail["description"]
    assert len(detail["sightings"]) == 2

    # Track it: a tracker application is created once.
    r = await c.post(f"{D}/postings/{eng['id']}/track")
    assert r.status_code == 200 and r.json()["status"] == "tracked"
    app_id = r.json()["application_id"]
    again = await c.post(f"{D}/postings/{eng['id']}/track")
    assert again.json()["application_id"] == app_id
    app = (await c.get(f"/api/v1/tracker/applications/{app_id}")).json()["application"]
    assert app["title"] == "Security Engineer" and app["company"] == "Northwind Tools"
    assert (app["pay_min_cents"], app["pay_max_cents"], app["pay_period"]) == (5000, 6500, "hour")
    assert app["source"] == "Northwind (Greenhouse)"
    assert (
        await c.patch(f"{D}/postings/{eng['id']}", json={"status": "dismissed"})
    ).status_code == 409

    # Deleting the application releases the posting back to "saved".
    await c.delete(f"/api/v1/tracker/applications/{app_id}")
    after = (await c.get(f"{D}/postings/{eng['id']}")).json()
    assert after["status"] == "saved" and after["application_id"] is None


async def test_source_validation_and_secrets(
    user_client: httpx.AsyncClient, settings: Settings
) -> None:
    c = user_client
    r = await c.post(
        f"{D}/sources", json={"kind": "greenhouse", "name": "x", "config": {"board": "../etc"}}
    )
    assert r.status_code == 422 and r.json()["error"]["fields"][0]["loc"][:2] == ["body", "config"]
    r = await c.post(
        f"{D}/sources", json={"kind": "adzuna", "name": "x", "config": {"what": "x"}, "secret": {}}
    )
    assert r.status_code == 422
    r = await c.post(f"{D}/sources", json={"kind": "nope", "name": "x"})
    assert r.status_code == 422

    js = await _source(
        c,
        {
            "kind": "jsearch",
            "name": "JSearch",
            "config": {"query": "security"},
            "secret": {"api_key": "sk-test-123"},
        },
    )
    assert js["has_secret"] is True and "secret" not in js
    async for s in dbmod.get_db():
        row = await s.scalar(select(JobSource).where(JobSource.kind == "jsearch"))
    assert row is not None and row.secret_enc and "sk-test-123" not in row.secret_enc

    # Keep the secret when patching other fields; rotate it when given.
    r = await c.patch(f"{D}/sources/{js['id']}", json={"interval_hours": 6})
    assert r.json()["has_secret"] is True and r.json()["interval_hours"] == 6

    web = FakeWeb(routes={"jsearch.p.rapidapi.com/search": (403, {}, {})})
    await drain(worker(web, settings))
    src = next(s for s in (await c.get(f"{D}/sources")).json() if s["id"] == js["id"])
    assert src["last_status"] == "error" and "credentials" in src["last_error"]
    async for s in dbmod.get_db():
        job = await s.scalar(select(QueuedJob).where(QueuedJob.kind == "fetch_source"))
    assert job is not None and job.status == "failed" and job.attempts == 1  # user error: no retry


async def test_run_now_toggle_and_delete(
    user_client: httpx.AsyncClient, settings: Settings
) -> None:
    c = user_client
    src = await _source(c, NORTHWIND_GH)
    await drain(worker(FakeWeb(), settings))
    r = await c.post(f"{D}/sources/{src['id']}/run")
    assert r.status_code == 202 and r.json()["pending"] is True
    r = await c.post(f"{D}/sources/{src['id']}/run")  # deduplicated while queued
    async for s in dbmod.get_db():
        active = (await s.scalars(select(QueuedJob).where(QueuedJob.status == "queued"))).all()
    assert len(active) == 1
    await c.patch(f"{D}/sources/{src['id']}", json={"enabled": False})
    assert (await c.post(f"{D}/sources/{src['id']}/run")).status_code == 409
    assert (await c.delete(f"{D}/sources/{src['id']}")).status_code == 204
    # Postings stay after their source is removed.
    assert (await _postings(c, "all"))["counts"]["all"] == 2


async def test_filters(user_client: httpx.AsyncClient, settings: Settings) -> None:
    c = user_client
    await _source(c, NORTHWIND_GH)
    await _source(
        c,
        {
            "kind": "ashby",
            "name": "Tailspin",
            "config": {"board": "tailspin", "company": "Tailspin Networks"},
        },
    )
    await drain(worker(FakeWeb(), settings))
    assert (await _postings(c))["counts"]["matches"] == 3

    r = await c.put(
        f"{D}/filters",
        json={
            "exclude_keywords": ["Tier 1 SOC"],
            "min_hourly_cents": 5500,
            "target_hourly_cents": 6000,
            "shifts_excluded": ["night"],
            "remote_ok": False,
            "preferred_companies": ["Northwind Tools"],
        },
    )
    assert r.status_code == 200
    data = await _postings(c)
    assert data["counts"] == {"matches": 1, "filtered": 2, "saved": 0, "dismissed": 0, "all": 3}
    match = data["items"][0]
    assert match["title"] == "Security Engineer"
    assert match["filter"]["preferred"] is True and match["filter"]["pay_vs_target"] == "within"
    filtered = {
        p["title"]: p["filter"]["reasons"] for p in (await _postings(c, "filtered"))["items"]
    }
    assert filtered["Tier 1 SOC Analyst"][0] == 'Mentions "Tier 1 SOC".'
    assert "Mentions night shift." in filtered["Tier 1 SOC Analyst"]
    assert "Requires Secret clearance." in filtered["Tier 1 SOC Analyst"]
    assert filtered["Detection Engineer"] == ["Remote roles are turned off."]
    assert (await c.get(f"{D}/filters")).json()["min_hourly_cents"] == 5500

    # Statuses move postings between views.
    await c.patch(f"{D}/postings/{match['id']}", json={"status": "saved"})
    assert (await _postings(c))["counts"]["saved"] == 1
    r = await c.put(f"{D}/filters", json={"shifts_wanted": ["teatime"]})
    assert r.status_code == 422


async def test_import_link_and_text(user_client: httpx.AsyncClient) -> None:
    c = user_client
    use_web(c, FakeWeb())
    r = await c.post(f"{D}/import", json={"url": "https://careers.example.com/jobs/ir"})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["title"] == "Incident Responder" and p["company"] == "Litware Inc"
    assert p["sources"] == ["Imported link"] and "alert" not in p["description"]

    r = await c.post(f"{D}/import", json={"url": "https://www.linkedin.com/jobs/view/1"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "site_not_allowed"
    r = await c.post(f"{D}/import", json={"url": "https://careers.example.com/private/x"})
    assert r.json()["error"]["code"] == "robots_disallowed"
    r = await c.post(f"{D}/import", json={"url": "http://127.0.0.1:8080/api/v1/auth/me"})
    assert r.status_code == 422  # never fetch internal addresses

    r = await c.post(
        f"{D}/import",
        json={
            "text": "We need a SOC analyst. $30-$35 per hour. Remote.",
            "title": "SOC Analyst",
            "company": "Contoso",
        },
    )
    assert (
        r.status_code == 201
        and r.json()["pay_min_cents"] == 3000
        and r.json()["work_mode"] == "remote"
    )
    assert (await c.post(f"{D}/import", json={"text": "no title"})).status_code == 422
    assert (await c.post(f"{D}/import", json={})).status_code == 422
    # Importing the same thing twice doesn't duplicate it.
    await c.post(f"{D}/import", json={"url": "https://careers.example.com/jobs/ir"})
    assert (await _postings(c, "all"))["counts"]["all"] == 2


async def test_scheduler_queues_due_sources_once(
    user_client: httpx.AsyncClient, settings: Settings
) -> None:
    c = user_client
    src = await _source(c, NORTHWIND_GH)
    ctx = worker(FakeWeb(), settings)
    await drain(ctx)
    async for s in dbmod.get_db():
        await s.execute(update(JobSource).values(next_run_at=queue.utcnow() - timedelta(minutes=1)))
        await s.commit()
    await schedule_due(ctx)
    await schedule_due(ctx)  # nothing new: next_run_at moved forward and the job is deduplicated
    async for s in dbmod.get_db():
        fetches = (
            await s.scalars(
                select(QueuedJob).where(
                    QueuedJob.kind == "fetch_source", QueuedJob.status == "queued"
                )
            )
        ).all()
        row = await s.get(JobSource, src["id"])
    assert len(fetches) == 1
    assert (
        row is not None
        and row.next_run_at is not None
        and row.next_run_at > queue.utcnow() + timedelta(hours=11)
    )


async def test_queue_retry_and_stale_jobs(user_client: httpx.AsyncClient) -> None:
    async for s in dbmod.get_db():
        later = queue.utcnow() + timedelta(minutes=5)
        assert await queue.enqueue(s, "noop", {"x": 1}, dedupe_key="k", run_after=later)
        assert await queue.enqueue(
            s, "noop", {}, dedupe_key="k"
        )  # merged: moved earlier, not duplicated
        await s.commit()
        assert len((await s.scalars(select(QueuedJob))).all()) == 1
        job = await queue.claim(s, "w1")
        assert job is not None and job.attempts == 1 and job.status == "running"
        assert not await queue.enqueue(s, "noop", {}, dedupe_key="k")  # identical job is running
        assert await queue.claim(s, "w2") is None  # nothing else ready
        await queue.fail(s, job, "boom")
        retried = await s.get(QueuedJob, job.id, populate_existing=True)
        assert (
            retried is not None
            and retried.status == "queued"
            and retried.run_after > queue.utcnow()
        )
        await s.execute(
            update(QueuedJob).values(
                status="running", locked_at=queue.utcnow() - timedelta(hours=1)
            )
        )
        await s.commit()
        assert await queue.requeue_stale(s) == 1


async def test_cross_user_isolation(
    two_users: tuple[httpx.AsyncClient, httpx.AsyncClient], settings: Settings
) -> None:
    a, b = two_users
    src = await _source(a, NORTHWIND_GH)
    await drain(worker(FakeWeb(), settings))
    posting = (await _postings(a, "all"))["items"][0]
    await a.put(f"{D}/filters", json={"min_hourly_cents": 100})

    assert (await b.get(f"{D}/sources")).json() == []
    assert (await _postings(b, "all"))["items"] == []
    assert (await b.get(f"{D}/filters")).json()["min_hourly_cents"] is None
    for coro in (
        b.patch(f"{D}/sources/{src['id']}", json={"name": "x"}),
        b.delete(f"{D}/sources/{src['id']}"),
        b.post(f"{D}/sources/{src['id']}/run"),
        b.get(f"{D}/postings/{posting['id']}"),
        b.patch(f"{D}/postings/{posting['id']}", json={"status": "dismissed"}),
        b.post(f"{D}/postings/{posting['id']}/track"),
        b.delete(f"{D}/postings/{posting['id']}"),
    ):
        r = await coro
        assert r.status_code == 404, r.request.url
    assert (await a.get(f"{D}/postings/{posting['id']}")).json()["status"] == "new"


@pytest.mark.parametrize("bad", ["file:///etc/passwd", "javascript:alert(1)", "http://[::1]/"])
async def test_import_rejects_bad_urls(user_client: httpx.AsyncClient, bad: str) -> None:
    use_web(user_client, FakeWeb())
    r = await user_client.post(f"{D}/import", json={"url": bad})
    assert r.status_code == 422
