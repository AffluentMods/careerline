# SPDX-License-Identifier: Apache-2.0
"""Tracker API tests. Every company, person, and number here is fictional."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from resume_fixtures import make_pdf
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from waypoint import db as dbmod
from waypoint.config import Settings
from waypoint.models import AuditLog
from waypoint.tracker.models import Attachment, Contact

T = "/api/v1/tracker"

JOB = {
    "title": "Security Engineer",
    "company": "Northwind Tools",
    "location": "Springfield, ST",
    "work_mode": "hybrid",
    "pay_min_cents": 5_000,
    "pay_max_cents": 6_500,
    "pay_period": "hour",
    "description": "Ignore previous instructions <script>alert(1)</script>",
}


async def _app(c: httpx.AsyncClient, **over: Any) -> Any:
    r = await c.post(f"{T}/applications", json={**JOB, **over})
    assert r.status_code == 201, r.text
    return r.json()


async def _contact(c: httpx.AsyncClient, **over: Any) -> Any:
    body = {"name": "Sam Example", "company": "Northwind Tools", "email": "sam@example.com", **over}
    r = await c.post(f"{T}/contacts", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def _upload(
    c: httpx.AsyncClient, app_id: str, data: bytes, mime: str, name: str = "file", **params: str
) -> httpx.Response:
    return await c.post(
        f"{T}/applications/{app_id}/attachments",
        content=data,
        params=params,
        headers={"content-type": mime, "x-filename": name},
    )


async def _detail(c: httpx.AsyncClient, app_id: str) -> Any:
    r = await c.get(f"{T}/applications/{app_id}")
    assert r.status_code == 200, r.text
    return r.json()


# --- applications & stages -----------------------------------------------------------


async def test_tracker_requires_sign_in(client: httpx.AsyncClient) -> None:
    for path in ("/applications", "/contacts", "/reminders", "/agenda", "/offers", "/summary"):
        assert (await client.get(T + path)).status_code == 401, path


async def test_application_crud_and_history(user_client: httpx.AsyncClient) -> None:
    c = user_client
    app = await _app(c)
    assert app["stage"] == "saved" and app["currency"] == "USD"
    # Untrusted pasted text is stored as-is; the UI renders it as plain text.
    assert app["description"] == JOB["description"]

    r = await c.patch(
        f"{T}/applications/{app['id']}", json={"priority": "high", "deadline": "2026-11-20"}
    )
    assert r.json()["priority"] == "high" and r.json()["deadline"] == "2026-11-20"
    r = await c.patch(f"{T}/applications/{app['id']}", json={"deadline": None, "title": None})
    assert r.json()["deadline"] is None and r.json()["title"] == "Security Engineer"
    r = await c.patch(f"{T}/applications/{app['id']}", json={"pay_min_cents": 9_000})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_pay"

    r = await c.post(
        f"{T}/applications/{app['id']}/stage",
        json={"stage": "applied", "note": "Applied via careers site", "today": "2026-11-04"},
    )
    assert r.json()["stage"] == "applied" and r.json()["applied_on"] == "2026-11-04"
    await c.post(f"{T}/applications/{app['id']}/stage", json={"stage": "applied"})  # no-op
    await c.post(f"{T}/applications/{app['id']}/stage", json={"stage": "screening"})

    events = (await _detail(c, app["id"]))["events"]
    stage_events = [e for e in events if e["kind"] == "stage_change"]
    assert [(e["data"]["from"], e["data"]["to"]) for e in stage_events] == [
        ("applied", "screening"),
        ("saved", "applied"),
        (None, "saved"),
    ]
    assert stage_events[1]["body"] == "Applied via careers site"

    # Stage history can't be rewritten.
    sid = stage_events[0]["id"]
    assert (await c.patch(f"{T}/events/{sid}", json={"body": "x"})).status_code == 409
    assert (await c.delete(f"{T}/events/{sid}")).status_code == 409


async def test_list_filters_and_board_summary(user_client: httpx.AsyncClient) -> None:
    c = user_client
    a = await _app(c)
    await _app(c, title="SOC Analyst", company="Contoso Ltd", stage="applied")
    rows = (await c.get(f"{T}/applications")).json()
    assert {r["title"] for r in rows} == {"Security Engineer", "SOC Analyst"}
    assert [r["title"] for r in (await c.get(f"{T}/applications?stage=applied")).json()] == [
        "SOC Analyst"
    ]
    assert [
        r["title"] for r in (await c.get(f"{T}/applications", params={"q": "contoso"})).json()
    ] == ["SOC Analyst"]
    await c.post(
        f"{T}/reminders",
        json={"title": "Email recruiter", "due_on": "2026-11-10", "application_id": a["id"]},
    )
    row = next(r for r in (await c.get(f"{T}/applications")).json() if r["id"] == a["id"])
    assert row["next_due"]["title"] == "Email recruiter"
    assert row["has_offer"] is False and row["contact_count"] == 0

    summary = (await c.get(f"{T}/summary", params={"today": "2026-11-10"})).json()
    assert summary["stages"]["saved"] == 1 and summary["stages"]["applied"] == 1
    assert summary["open_total"] == 2 and summary["due_today"] == 1 and summary["overdue"] == 0


async def test_events_crud_keeps_files(user_client: httpx.AsyncClient) -> None:
    c = user_client
    app = await _app(c)
    r = await c.post(
        f"{T}/applications/{app['id']}/events",
        json={
            "kind": "sent",
            "title": "Application sent",
            "data": {"materials": "Resume v3 + cover letter"},
        },
    )
    assert r.status_code == 201
    event = r.json()
    assert event["data"] == {"materials": "Resume v3 + cover letter"}
    r = await _upload(
        c, app["id"], make_pdf(["Resume"]), "application/pdf", "resume.pdf", event_id=event["id"]
    )
    assert r.status_code == 201, r.text

    detail = await _detail(c, app["id"])
    sent = next(e for e in detail["events"] if e["id"] == event["id"])
    assert [f["filename"] for f in sent["attachments"]] == ["resume.pdf"]

    r = await c.patch(f"{T}/events/{event['id']}", json={"body": "Also sent portfolio link"})
    assert r.json()["body"] == "Also sent portfolio link"
    assert len(r.json()["attachments"]) == 1

    r = await c.post(f"{T}/applications/{app['id']}/events", json={"kind": "stage_change"})
    assert r.status_code == 422  # users can't forge stage history

    assert (await c.delete(f"{T}/events/{event['id']}")).status_code == 204
    detail = await _detail(c, app["id"])
    assert [f["filename"] for f in detail["attachments"]] == ["resume.pdf"]  # file kept, unlinked


async def test_delete_application_cascades_and_is_audited(user_client: httpx.AsyncClient) -> None:
    c = user_client
    app = await _app(c)
    contact = await _contact(c)
    await c.put(
        f"{T}/applications/{app['id']}/contacts/{contact['id']}", json={"role": "recruiter"}
    )
    await c.post(
        f"{T}/reminders", json={"title": "x", "due_on": "2026-11-10", "application_id": app["id"]}
    )
    await _upload(c, app["id"], b"call notes", "text/plain", "notes.txt")
    await c.put(
        f"{T}/applications/{app['id']}/offer",
        json={"base_cents": 10_000_000, "base_period": "year"},
    )

    assert (await c.delete(f"{T}/applications/{app['id']}")).status_code == 204
    assert (await c.get(f"{T}/applications/{app['id']}")).status_code == 404
    assert (await c.get(f"{T}/reminders")).json() == []
    assert (await c.get(f"{T}/offers")).json() == []
    # Contacts are kept.
    assert (await c.get(f"{T}/contacts/{contact['id']}")).json()["links"] == []
    async for s in dbmod.get_db():
        assert await s.scalar(select(Attachment)) is None
        event = await s.scalar(
            select(AuditLog).where(AuditLog.event == "tracker.application_deleted")
        )
    assert event is not None and event.details["files"] == 1


# --- contacts, links, referrals --------------------------------------------------------


async def test_contacts_encrypted_and_linked(user_client: httpx.AsyncClient) -> None:
    c = user_client
    contact = await _contact(c, phone="555-0142", linkedin_url="https://example.com/in/sam")
    assert contact["email"] == "sam@example.com" and contact["phone"] == "555-0142"
    async for s in dbmod.get_db():
        row = await s.scalar(select(Contact))
    assert row is not None and row.email_enc and row.email_enc.startswith("v1:")
    assert "sam@example.com" not in (row.email_enc or "") and "555-0142" not in (
        row.phone_enc or ""
    )

    app = await _app(c)
    r = await c.put(
        f"{T}/applications/{app['id']}/contacts/{contact['id']}", json={"role": "recruiter"}
    )
    assert r.json()["role"] == "recruiter"
    r = await c.put(
        f"{T}/applications/{app['id']}/contacts/{contact['id']}", json={"role": "hiring_manager"}
    )
    assert r.json()["contact"]["links"][0]["role"] == "hiring_manager"

    r = await c.patch(f"{T}/contacts/{contact['id']}", json={"title": "Recruiter", "phone": ""})
    assert r.json()["title"] == "Recruiter" and r.json()["phone"] == ""
    assert r.json()["email"] == "sam@example.com"
    assert [x["name"] for x in (await c.get(f"{T}/contacts", params={"q": "recruit"})).json()] == [
        "Sam Example"
    ]
    assert (await c.post(f"{T}/contacts", json={"name": "x", "email": "nope"})).status_code == 422

    assert (
        await c.delete(f"{T}/applications/{app['id']}/contacts/{contact['id']}")
    ).status_code == 204
    assert (await _detail(c, app["id"]))["contacts"] == []


async def test_referrals(user_client: httpx.AsyncClient) -> None:
    c = user_client
    app = await _app(c)
    contact = await _contact(c)
    body = {
        "contact_id": contact["id"],
        "status": "asked",
        "asked_on": "2026-11-01",
        "follow_up_on": "2026-11-08",
    }
    r = await c.post(f"{T}/applications/{app['id']}/referrals", json=body)
    assert r.status_code == 201 and r.json()["contact_name"] == "Sam Example"
    ref = r.json()
    assert (await c.post(f"{T}/applications/{app['id']}/referrals", json=body)).status_code == 409

    detail = await _detail(c, app["id"])
    assert [x["role"] for x in detail["contacts"]] == ["referrer"]  # auto-linked
    agenda = (await c.get(f"{T}/agenda", params={"today": "2026-11-04"})).json()
    assert [i["source"] for i in agenda] == ["referral_follow_up"]
    assert agenda[0]["contact_name"] == "Sam Example"

    r = await c.patch(
        f"{T}/referrals/{ref['id']}", json={"status": "submitted", "referred_on": "2026-11-05"}
    )
    assert r.json()["status"] == "submitted"
    assert (await c.get(f"{T}/agenda", params={"today": "2026-11-04"})).json() == []
    assert (await c.delete(f"{T}/referrals/{ref['id']}")).status_code == 204


async def test_deleting_contact_keeps_reminders(user_client: httpx.AsyncClient) -> None:
    c = user_client
    contact = await _contact(c)
    r = await c.post(
        f"{T}/reminders",
        json={"title": "Call Sam", "due_on": "2026-11-06", "contact_id": contact["id"]},
    )
    reminder = r.json()
    assert (await c.delete(f"{T}/contacts/{contact['id']}")).status_code == 204
    kept = (await c.get(f"{T}/reminders")).json()
    assert [(x["id"], x["contact_id"]) for x in kept] == [(reminder["id"], None)]


# --- reminders & agenda ----------------------------------------------------------------


async def test_reminders_and_agenda(user_client: httpx.AsyncClient) -> None:
    c = user_client
    app = await _app(c, deadline="2026-11-12")
    late = (
        await c.post(f"{T}/reminders", json={"title": "Overdue thing", "due_on": "2026-11-01"})
    ).json()
    await c.post(f"{T}/reminders", json={"title": "Far away", "due_on": "2027-03-01"})
    await c.post(
        f"{T}/reminders",
        json={
            "title": "Interview prep",
            "kind": "interview",
            "due_on": "2026-11-04",
            "due_time": "09:30",
        },
    )

    agenda = (await c.get(f"{T}/agenda", params={"today": "2026-11-04", "days": 14})).json()
    assert [i["title"] for i in agenda] == [
        "Overdue thing",
        "Interview prep",
        "Apply by: Security Engineer",
    ]
    assert agenda[2]["application_id"] == app["id"]

    r = await c.patch(f"{T}/reminders/{late['id']}", json={"done": True})
    assert r.json()["done_at"] is not None
    assert len((await c.get(f"{T}/reminders")).json()) == 2
    assert len((await c.get(f"{T}/reminders", params={"include_done": "true"})).json()) == 3
    r = await c.patch(f"{T}/reminders/{late['id']}", json={"done": False, "due_on": "2026-11-05"})
    assert r.json()["done_at"] is None and r.json()["due_on"] == "2026-11-05"

    # Deadlines stop showing once an application is closed.
    await c.post(f"{T}/applications/{app['id']}/stage", json={"stage": "rejected"})
    titles = [
        i["title"] for i in (await c.get(f"{T}/agenda", params={"today": "2026-11-04"})).json()
    ]
    assert "Apply by: Security Engineer" not in titles

    r = await c.post(
        f"{T}/reminders",
        json={"title": "x", "due_on": "2026-11-05", "application_id": str(uuid.uuid4())},
    )
    assert r.status_code == 404


# --- attachments ---------------------------------------------------------------------------


async def test_attachment_roundtrip_encrypted(user_client: httpx.AsyncClient) -> None:
    c = user_client
    app = await _app(c)
    pdf = make_pdf(["Offer letter for a fictional person"])
    r = await _upload(
        c, app["id"], pdf, "application/pdf", "Caf%C3%A9%20offer%20%E2%80%94%20final.pdf"
    )
    assert r.status_code == 201, r.text
    meta = r.json()
    assert meta["filename"] == "Café offer _ final.pdf" and meta["size_bytes"] == len(pdf)

    async for s in dbmod.get_db():
        row = await s.scalar(select(Attachment))
    assert row is not None and row.content_enc.startswith(b"v1:")
    assert b"Offer letter" not in row.content_enc

    r = await c.get(f"{T}/attachments/{meta['id']}/download")
    assert r.status_code == 200 and r.content == pdf
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].startswith("attachment;")
    assert "sandbox" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"

    assert (await c.delete(f"{T}/attachments/{meta['id']}")).status_code == 204
    assert (await c.get(f"{T}/attachments/{meta['id']}/download")).status_code == 404


async def test_attachment_rejections(user_client: httpx.AsyncClient) -> None:
    c = user_client
    app = await _app(c)
    r = await _upload(c, app["id"], b"<html><script>alert(1)</script>", "text/html")
    assert r.status_code == 415
    r = await _upload(c, app["id"], b"<svg onload=alert(1)>", "image/png")
    assert r.status_code == 415
    r = await _upload(c, app["id"], b"", "text/plain")
    assert r.status_code == 422
    other = await _app(c, title="Other")
    ev = (await c.post(f"{T}/applications/{other['id']}/events", json={"kind": "note"})).json()
    r = await _upload(c, app["id"], b"x", "text/plain", event_id=ev["id"])
    assert r.status_code == 404  # event belongs to a different application


async def test_attachment_size_and_quota(
    app_factory: Callable[[Settings], httpx.AsyncClient], make_settings: Callable[..., Settings]
) -> None:
    s = make_settings(
        attachment_max_bytes=20_000, attachment_quota_bytes=30_000, max_body_bytes=5_000
    )
    c = app_factory(s)
    await c.get("/api/v1/auth/status")
    await c.post(
        "/api/v1/auth/register",
        json={
            "email": "jordan@example.com",
            "password": "correct horse battery",
            "display_name": "J",
        },
    )
    app = await _app(c)
    assert (await _upload(c, app["id"], b"a" * 25_000, "text/plain")).status_code == 413
    assert (await _upload(c, app["id"], b"a" * 15_000, "text/plain")).status_code == 201
    r = await _upload(c, app["id"], b"a" * 16_000, "text/plain")
    assert r.status_code == 413 and r.json()["error"]["code"] == "storage_quota"
    # The bigger limit applies to the upload route only.
    r = await c.post(
        f"{T}/applications/{app['id']}/events", json={"kind": "note", "body": "a" * 6_000}
    )
    assert r.status_code == 413


# --- offers ------------------------------------------------------------------------------


async def test_offers_and_comparison(user_client: httpx.AsyncClient) -> None:
    c = user_client
    a = await _app(c, stage="offer")
    b = await _app(c, title="SOC Analyst", stage="offer")
    r = await c.put(
        f"{T}/applications/{a['id']}/offer",
        json={
            "base_cents": 11_000_000,
            "base_period": "year",
            "bonus_cents": 500_000,
            "pto_days": 20,
            "deadline": "2026-11-09",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["hourly_cents"] == 5_288 and r.json()["total_annual_cents"] == 11_500_000
    await c.put(
        f"{T}/applications/{b['id']}/offer",
        json={
            "base_cents": 5_500,
            "base_period": "hour",
            "hours_per_week": 36,
            "commute_minutes": 15,
        },
    )
    offers = (await c.get(f"{T}/offers")).json()
    assert [(o["application_title"], o["annual_cents"]) for o in offers] == [
        ("Security Engineer", 11_000_000),
        ("SOC Analyst", 5_500 * 36 * 52),
    ]
    agenda = (await c.get(f"{T}/agenda", params={"today": "2026-11-04"})).json()
    assert [i["source"] for i in agenda] == ["offer_deadline"]
    assert (await _detail(c, a["id"]))["offer"]["pto_days"] == 20

    r = await c.put(
        f"{T}/applications/{a['id']}/offer", json={"base_cents": 12_000_000, "base_period": "year"}
    )
    assert r.json()["bonus_cents"] is None  # PUT replaces the whole offer
    assert (await c.delete(f"{T}/applications/{a['id']}/offer")).status_code == 204
    assert len((await c.get(f"{T}/offers")).json()) == 1


# --- isolation ---------------------------------------------------------------------------


async def test_cross_user_isolation(two_users: tuple[httpx.AsyncClient, httpx.AsyncClient]) -> None:
    a, b = two_users
    app = await _app(a)
    contact = await _contact(a)
    ev = (
        await a.post(
            f"{T}/applications/{app['id']}/events", json={"kind": "note", "body": "private"}
        )
    ).json()
    file = (await _upload(a, app["id"], b"secret notes", "text/plain")).json()
    ref = (
        await a.post(f"{T}/applications/{app['id']}/referrals", json={"contact_id": contact["id"]})
    ).json()
    rem = (await a.post(f"{T}/reminders", json={"title": "x", "due_on": "2026-11-05"})).json()
    await a.put(
        f"{T}/applications/{app['id']}/offer", json={"base_cents": 100, "base_period": "hour"}
    )

    for path in ("/applications", "/contacts", "/reminders", "/offers"):
        assert (await b.get(T + path)).json() == [], path
    assert (await b.get(f"{T}/agenda", params={"today": "2026-11-04"})).json() == []
    assert (await b.get(f"{T}/summary")).json()["open_total"] == 0

    b_app = await _app(b)
    b_contact = await _contact(b)
    attempts = [
        b.get(f"{T}/applications/{app['id']}"),
        b.patch(f"{T}/applications/{app['id']}", json={"title": "hijack"}),
        b.post(f"{T}/applications/{app['id']}/stage", json={"stage": "rejected"}),
        b.delete(f"{T}/applications/{app['id']}"),
        b.post(f"{T}/applications/{app['id']}/events", json={"kind": "note"}),
        b.patch(f"{T}/events/{ev['id']}", json={"body": "hijack"}),
        b.delete(f"{T}/events/{ev['id']}"),
        b.get(f"{T}/attachments/{file['id']}/download"),
        b.delete(f"{T}/attachments/{file['id']}"),
        _upload(b, app["id"], b"x", "text/plain"),
        b.get(f"{T}/contacts/{contact['id']}"),
        b.patch(f"{T}/contacts/{contact['id']}", json={"name": "hijack"}),
        b.delete(f"{T}/contacts/{contact['id']}"),
        b.put(f"{T}/applications/{b_app['id']}/contacts/{contact['id']}", json={"role": "other"}),
        b.put(f"{T}/applications/{app['id']}/contacts/{b_contact['id']}", json={"role": "other"}),
        b.post(f"{T}/applications/{b_app['id']}/referrals", json={"contact_id": contact["id"]}),
        b.patch(f"{T}/referrals/{ref['id']}", json={"status": "declined"}),
        b.delete(f"{T}/referrals/{ref['id']}"),
        b.patch(f"{T}/reminders/{rem['id']}", json={"done": True}),
        b.delete(f"{T}/reminders/{rem['id']}"),
        b.post(
            f"{T}/reminders",
            json={"title": "x", "due_on": "2026-11-05", "application_id": app["id"]},
        ),
        b.post(
            f"{T}/reminders",
            json={"title": "x", "due_on": "2026-11-05", "contact_id": contact["id"]},
        ),
        b.put(f"{T}/applications/{app['id']}/offer", json={"base_cents": 1, "base_period": "hour"}),
        b.delete(f"{T}/applications/{app['id']}/offer"),
    ]
    for coro in attempts:
        r = await coro
        assert r.status_code == 404, (r.request.method, r.request.url)

    detail = await _detail(a, app["id"])
    assert detail["application"]["title"] == "Security Engineer"
    assert detail["application"]["stage"] == "saved"
    assert any(e["body"] == "private" for e in detail["events"])
    assert detail["referrals"][0]["status"] == "to_ask"
    assert detail["offer"]["base_cents"] == 100
    assert (await a.get(f"{T}/attachments/{file['id']}/download")).content == b"secret notes"


async def test_database_blocks_cross_user_links(
    two_users: tuple[httpx.AsyncClient, httpx.AsyncClient],
) -> None:
    a, b = two_users
    app = await _app(a)
    b_contact = await _contact(b)
    async for s in dbmod.get_db():
        a_user = await s.scalar(text("SELECT id FROM users WHERE email = 'jordan@example.com'"))
        statements = [
            (
                "INSERT INTO application_contacts (application_id, contact_id, user_id, role) "
                "VALUES (:a, :c, :u, 'other')"
            ),
            (
                "INSERT INTO referrals (id, application_id, contact_id, user_id, status, notes) "
                "VALUES (gen_random_uuid(), :a, :c, :u, 'asked', '')"
            ),
            (
                "INSERT INTO reminders (id, user_id, title, kind, due_on, contact_id, notes) "
                "VALUES (gen_random_uuid(), :u, 't', 'task', '2026-11-05', :c, '')"
            ),
        ]
        for sql in statements:
            with pytest.raises(IntegrityError):
                await s.execute(text(sql), {"a": app["id"], "c": b_contact["id"], "u": a_user})
            await s.rollback()
