# SPDX-License-Identifier: Apache-2.0
"""Notifications: settings, encryption, escaping, SSRF, the digest and match alerts."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from tests.conftest import TEST_PASSWORD
from tests.fake_web import FakeWeb
from waypoint import db
from waypoint.config import Settings
from waypoint.discovery.models import JobPosting
from waypoint.notify import senders
from waypoint.notify.models import NotificationLog, NotificationSettings
from waypoint.notify.senders import Message
from waypoint.security.crypto import FieldCipher
from waypoint.worker import handlers  # noqa: F401  (registers the notification jobs)
from waypoint.worker.runner import WorkerContext, drain, schedule_due

N = "/api/v1/notifications"
NTFY = "https://ntfy.example.com/waypoint-jordan-4821"
DISCORD = "https://discord.com/api/webhooks/123/secret-token"


@pytest.fixture
def web() -> FakeWeb:
    return FakeWeb(
        {
            "ntfy.example.com/": (200, {"id": "x"}, {}),
            "discord.com/api/webhooks/123/secret-token": (204, b"", {}),
            "hooks.slack.com/services/T/B/x": (200, "ok", {}),
            "hooks.internal.example/notify": (200, "ok", {}),
        },
        addresses={"hooks.internal.example": ["10.0.0.5"]},
    )


@pytest.fixture
def mail() -> Iterator[list[EmailMessage]]:
    sent: list[EmailMessage] = []
    senders.set_email_transport(lambda _settings, msg: sent.append(msg))
    yield sent
    senders.set_email_transport(None)


def _use(c: httpx.AsyncClient, web: FakeWeb) -> None:
    c._transport.app.state.fetcher_factory = web.factory()  # type: ignore[attr-defined]


def _worker(settings: Settings, web: FakeWeb) -> WorkerContext:
    return WorkerContext(
        sessionmaker=db.get_sessionmaker(),
        settings=settings,
        cipher=FieldCipher(settings.encryption_keys.get_secret_value()),
        fetcher_factory=web.factory(),
    )


def _bodies(web: FakeWeb, host: str) -> list[Any]:
    return [json.loads(r.content) for r in web.requests if r.headers["host"] == host]


async def test_defaults_and_secret_storage(user_client: httpx.AsyncClient) -> None:
    c = user_client
    r = await c.get(f"{N}/settings")
    assert r.status_code == 200
    assert r.json()["digest_enabled"] is False and r.json()["timezone"] == "UTC"
    r = await c.put(
        f"{N}/settings",
        json={
            "timezone": "America/Los_Angeles",
            "ntfy_enabled": True,
            "ntfy_url": NTFY,
            "ntfy_token": "tk_secret",
            "webhook_enabled": True,
            "webhook_kind": "discord",
            "webhook_url": DISCORD,
        },
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ntfy_url"] == NTFY and out["has_ntfy_token"] is True
    assert out["webhook_hint"] == "https://discord.com/…"
    assert "secret-token" not in r.text and "tk_secret" not in r.text

    async with db.get_sessionmaker()() as s:
        row = await s.scalar(select(NotificationSettings))
        assert row is not None
        stored = " ".join(str(v) for v in vars(row).values())
        assert (
            "secret-token" not in stored
            and "tk_secret" not in stored
            and "ntfy.example" not in stored
        )

    # Omitted secrets are kept; "" removes them.
    r = await c.put(f"{N}/settings", json={"timezone": "UTC", "ntfy_token": ""})
    assert r.json()["has_ntfy_token"] is False and r.json()["ntfy_url"] == NTFY


@pytest.mark.parametrize(
    "body",
    [
        {"timezone": "Mars/Olympus"},
        {"digest_hour": 24},
        {"ntfy_url": "https://ntfy.sh/topic?x=1"},
        {"ntfy_url": "https://user:pw@ntfy.sh/topic"},
        {"ntfy_url": "ftp://ntfy.sh/topic"},
        {"ntfy_url": "https://ntfy.sh/"},
        {"webhook_kind": "discord", "webhook_url": "https://evil.example/api/webhooks/1/x"},
        {"webhook_kind": "slack", "webhook_url": "http://hooks.slack.com/services/x"},
        {"email_to": "not-an-email"},
    ],
)
async def test_rejects_bad_settings(user_client: httpx.AsyncClient, body: dict[str, Any]) -> None:
    r = await user_client.put(f"{N}/settings", json=body)
    assert r.status_code == 422, r.text


def test_payloads_escape_untrusted_text() -> None:
    msg = Message(
        kind="matches",
        title="1 new job match",
        lines=["@everyone [Claim prize](https://evil.example) <https://evil.example|click> **x**"],
        link="https://waypoint.example.com/jobs",
    )
    d = senders.discord_payload(msg)
    assert d["allowed_mentions"] == {"parse": []}
    assert "[Claim prize](" not in d["content"] and "https://evil" not in d["content"]
    assert "\\@everyone" in d["content"]
    s = senders.slack_payload(msg)
    assert "<https://evil.example|click>" not in s["text"] and "&lt;https" in s["text"]
    server, body = senders.ntfy_payload(NTFY, msg)
    assert server == "https://ntfy.example.com/" and body["topic"] == "waypoint-jordan-4821"
    assert body["click"] == "https://waypoint.example.com/jobs"
    assert "\n" not in senders.clean("line one\nline two\r\x00")


async def test_test_sends_on_each_channel(
    app_factory: Callable[[Settings], httpx.AsyncClient],
    make_settings: Callable[..., Settings],
    web: FakeWeb,
    mail: list[EmailMessage],
) -> None:
    s = make_settings(smtp_host="smtp.example.com", smtp_from="waypoint@example.com")
    c = app_factory(s)
    await c.get("/api/v1/auth/status")
    await c.post(
        "/api/v1/auth/register",
        json={"email": "jordan@example.com", "password": TEST_PASSWORD, "display_name": "J"},
    )
    _use(c, web)
    assert (await c.post(f"{N}/test", json={"channel": "ntfy"})).status_code == 409
    await c.put(
        f"{N}/settings",
        json={
            "email_enabled": True,
            "email_to": "Jordan@Example.com",
            "ntfy_enabled": True,
            "ntfy_url": NTFY,
            "ntfy_token": "tk_secret",
            "webhook_enabled": True,
            "webhook_kind": "discord",
            "webhook_url": DISCORD,
        },
    )
    for channel in ("email", "ntfy", "webhook"):
        r = await c.post(f"{N}/test", json={"channel": channel})
        assert r.json() == {"ok": True, "error": None}, channel
    assert mail[0]["To"] == "jordan@example.com"
    assert mail[0]["Subject"] == "Waypoint test notification"
    ntfy = next(r for r in web.requests if r.headers["host"] == "ntfy.example.com")
    assert ntfy.headers["authorization"] == "Bearer tk_secret"
    assert _bodies(web, "discord.com")[0]["allowed_mentions"] == {"parse": []}
    log = (await c.get(f"{N}/log")).json()
    assert {e["channel"] for e in log} == {"email", "ntfy", "webhook"}
    assert all(e["ok"] for e in log)


async def test_private_targets_blocked_for_non_owner(
    two_users: tuple[httpx.AsyncClient, httpx.AsyncClient], web: FakeWeb
) -> None:
    _, other = two_users
    _use(other, web)
    r = await other.put(
        f"{N}/settings",
        json={
            "webhook_enabled": True,
            "webhook_kind": "generic",
            "webhook_url": "https://hooks.internal.example/notify",
        },
    )
    assert r.json()["private_targets_allowed"] is False
    r = await other.post(f"{N}/test", json={"channel": "webhook"})
    assert r.json()["ok"] is False and "private network" in r.json()["error"]
    assert not any(r.headers["host"] == "hooks.internal.example" for r in web.requests)


async def test_daily_digest(
    user_client: httpx.AsyncClient, settings: Settings, web: FakeWeb
) -> None:
    c = user_client
    today = datetime.now(UTC).date()
    await c.post(
        "/api/v1/tracker/reminders",
        json={"title": "Follow up with Litware", "due_on": today.isoformat()},
    )
    await c.put(
        f"{N}/settings",
        json={"timezone": "UTC", "ntfy_enabled": True, "ntfy_url": NTFY},
    )
    hour = datetime.now(UTC).hour
    # Turned on after today's hour has passed: starts tomorrow, nothing now.
    if hour > 0:
        await c.put(
            f"{N}/settings",
            json={
                "timezone": "UTC",
                "digest_enabled": True,
                "digest_hour": hour - 1,
                "ntfy_enabled": True,
            },
        )
        ctx = _worker(settings, web)
        await schedule_due(ctx)
        await drain(ctx)
        assert _bodies(web, "ntfy.example.com") == []
        await c.put(f"{N}/settings", json={"timezone": "UTC", "ntfy_enabled": True})

    await c.put(
        f"{N}/settings",
        json={"timezone": "UTC", "digest_enabled": True, "digest_hour": hour, "ntfy_enabled": True},
    )
    ctx = _worker(settings, web)
    await schedule_due(ctx)
    await drain(ctx)
    sent = _bodies(web, "ntfy.example.com")
    assert len(sent) == 1
    assert sent[0]["title"].startswith("Waypoint: 1 thing to do")
    assert "Today: Follow up with Litware" in sent[0]["message"]
    # Once a day.
    await schedule_due(ctx)
    await drain(ctx)
    assert len(_bodies(web, "ntfy.example.com")) == 1


async def test_match_alerts(
    user_client: httpx.AsyncClient, settings: Settings, web: FakeWeb
) -> None:
    c = user_client
    await c.post("/api/v1/vault/entries", json={"kind": "skill", "data": {"name": "Python"}})
    for title in ("Python Developer", "Data Engineer"):
        r = await c.post(
            "/api/v1/discovery/import",
            json={"title": title, "company": "Litware", "text": "Requirements:\n- Python"},
        )
        assert r.status_code in (200, 201), r.text
    ctx = _worker(settings, web)
    await drain(ctx)  # scores the new postings (keyword scoring, no AI configured)
    await c.put(
        f"{N}/settings",
        json={
            "timezone": "UTC",
            "matches_enabled": True,
            "match_min_score": 0,
            "webhook_enabled": True,
            "webhook_kind": "discord",
            "webhook_url": DISCORD,
        },
    )
    await schedule_due(ctx)
    await drain(ctx)
    sent = _bodies(web, "discord.com")
    assert len(sent) == 1
    assert "2 new job matches" in sent[0]["content"]
    async with db.get_sessionmaker()() as s:
        marks = list(await s.scalars(select(JobPosting.notified_at)))
        assert marks and all(m is not None for m in marks)
    # Nothing new: no second alert.
    await schedule_due(ctx)
    await drain(ctx)
    assert len(_bodies(web, "discord.com")) == 1


async def test_demo_mode_blocks_sending(
    app_factory: Callable[[Settings], httpx.AsyncClient],
    make_settings: Callable[..., Settings],
) -> None:
    s = make_settings(demo_mode=True)
    c = app_factory(s)
    await c.get("/api/v1/auth/status")
    async with db.get_sessionmaker()() as session:
        from waypoint.demo import DEMO_EMAIL, DEMO_PASSWORD, seed_demo

        await seed_demo(session, FieldCipher(s.encryption_keys.get_secret_value()))
        await session.commit()
    r = await c.post("/api/v1/auth/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD})
    assert r.status_code == 200, r.text
    r = await c.put(f"{N}/settings", json={"ntfy_enabled": True, "ntfy_url": NTFY})
    assert r.status_code == 403
    assert (await c.post(f"{N}/test", json={"channel": "ntfy"})).status_code == 403
    async with db.get_sessionmaker()() as session:
        assert await session.scalar(select(NotificationLog.id)) is None


async def test_settings_are_per_user(
    two_users: tuple[httpx.AsyncClient, httpx.AsyncClient],
) -> None:
    a, b = two_users
    await a.put(f"{N}/settings", json={"ntfy_enabled": True, "ntfy_url": NTFY})
    assert (await b.get(f"{N}/settings")).json()["ntfy_url"] == ""
    assert (await b.get(f"{N}/log")).json() == []
    me = (await a.get("/api/v1/auth/me")).json()
    async with db.get_sessionmaker()() as s:
        rows = list(await s.scalars(select(NotificationSettings)))
        assert [r.user_id for r in rows] == [uuid.UUID(me["id"])]
