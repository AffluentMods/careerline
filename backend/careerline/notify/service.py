# SPDX-License-Identifier: Apache-2.0
"""Building and delivering notifications: the daily digest and new-match alerts."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from careerline.ai.models import JobScore
from careerline.config import Settings
from careerline.discovery.models import JobFilters, JobPosting
from careerline.discovery.service import evaluate
from careerline.net import SafeFetcher
from careerline.notify import senders
from careerline.notify.models import NotificationLog, NotificationSettings, setting_context
from careerline.notify.senders import Message, SendError
from careerline.security.crypto import FieldCipher
from careerline.tracker.service import agenda_items

MAX_MATCHES = 10
MATCH_WINDOW = timedelta(days=7)


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def local_now(row: NotificationSettings, now: datetime | None = None) -> datetime:
    return (now or datetime.now(UTC)).astimezone(zone(row.timezone))


async def get_row(db: AsyncSession, user_id: uuid.UUID) -> NotificationSettings:
    row = await db.get(NotificationSettings, user_id)
    if row is None:
        row = NotificationSettings(
            user_id=user_id,
            timezone="UTC",
            digest_enabled=False,
            digest_hour=8,
            matches_enabled=False,
            match_min_score=80,
            email_enabled=False,
            ntfy_enabled=False,
            webhook_enabled=False,
            webhook_kind="discord",
        )
        db.add(row)
    return row


@dataclass
class Channels:
    email_to: str = ""
    ntfy_url: str = ""
    ntfy_token: str = ""
    webhook_url: str = ""


def secrets_of(cipher: FieldCipher, row: NotificationSettings) -> Channels:
    def dec(value: str | None, field: str) -> str:
        return cipher.decrypt(value, setting_context(row.user_id, field)) if value else ""

    return Channels(
        email_to=dec(row.email_to_enc, "email_to"),
        ntfy_url=dec(row.ntfy_url_enc, "ntfy_url"),
        ntfy_token=dec(row.ntfy_token_enc, "ntfy_token"),
        webhook_url=dec(row.webhook_url_enc, "webhook_url"),
    )


def enabled_channels(row: NotificationSettings, ch: Channels, settings: Settings) -> list[str]:
    out = []
    if row.email_enabled and ch.email_to and settings.smtp_enabled:
        out.append("email")
    if row.ntfy_enabled and ch.ntfy_url:
        out.append("ntfy")
    if row.webhook_enabled and ch.webhook_url:
        out.append("webhook")
    return out


async def send_one(
    channel: str,
    row: NotificationSettings,
    ch: Channels,
    msg: Message,
    settings: Settings,
    fetcher: SafeFetcher,
) -> None:
    if channel == "email":
        if not ch.email_to:
            raise SendError("Add an email address first.")
        await senders.send_email(settings, ch.email_to, msg)
    elif channel == "ntfy":
        if not ch.ntfy_url:
            raise SendError("Add an ntfy topic address first.")
        await senders.send_ntfy(fetcher, ch.ntfy_url, ch.ntfy_token, msg)
    elif channel == "webhook":
        if not ch.webhook_url:
            raise SendError("Add a webhook address first.")
        await senders.send_webhook(fetcher, row.webhook_kind, ch.webhook_url, msg)
    else:  # pragma: no cover - validated by the API
        raise SendError("Unknown channel.")


async def deliver(
    db: AsyncSession,
    row: NotificationSettings,
    cipher: FieldCipher,
    settings: Settings,
    fetcher: SafeFetcher,
    msg: Message,
    channels: list[str] | None = None,
) -> dict[str, str | None]:
    """Send on each channel and log the outcome. Returns {channel: error or None}.
    The caller commits."""
    ch = secrets_of(cipher, row)
    results: dict[str, str | None] = {}
    for channel in channels if channels is not None else enabled_channels(row, ch, settings):
        try:
            await send_one(channel, row, ch, msg, settings, fetcher)
            results[channel] = None
        except SendError as exc:
            results[channel] = str(exc)
        db.add(
            NotificationLog(
                user_id=row.user_id,
                kind=msg.kind,
                channel=channel,
                ok=results[channel] is None,
                title=senders.clean(msg.title, 200),
                detail=(results[channel] or "")[:300],
            )
        )
    return results


def _link(settings: Settings, path: str) -> str:
    return f"{settings.public_url.rstrip('/')}{path}" if settings.public_url else ""


def _when(due: date, today: date) -> str:
    if due < today:
        days = (today - due).days
        return f"Overdue {days} day{'s' if days != 1 else ''}"
    if due == today:
        return "Today"
    if due == today + timedelta(days=1):
        return "Tomorrow"
    return due.strftime("%a %b ") + str(due.day)


async def build_digest(
    db: AsyncSession, user_id: uuid.UUID, today: date, settings: Settings
) -> Message | None:
    """What's overdue, due today, and due tomorrow. None when there's nothing to say."""
    items = await agenda_items(db, user_id, until=today + timedelta(days=1))
    if not items:
        return None
    items.sort(key=lambda i: (i.due_on, i.due_time is None, i.due_time))
    lines = []
    for i in items[:20]:
        where = f" ({i.company})" if i.company and i.company not in i.title else ""
        lines.append(f"{_when(i.due_on, today)}: {i.title}{where}")
    if len(items) > 20:
        lines.append(f"and {len(items) - 20} more")
    overdue = sum(i.due_on < today for i in items)
    title = f"CareerLine: {len(items)} thing{'s' if len(items) != 1 else ''} to do"
    if overdue:
        title += f" ({overdue} overdue)"
    return Message(kind="digest", title=title, lines=lines, link=_link(settings, "/tracker/agenda"))


async def match_candidates(
    db: AsyncSession, user_id: uuid.UUID, min_score: int, now: datetime
) -> list[tuple[JobPosting, int]]:
    rows = await db.execute(
        select(JobPosting, JobScore.score)
        .join(
            JobScore,
            (JobScore.user_id == JobPosting.user_id) & (JobScore.posting_id == JobPosting.id),
        )
        .where(
            JobPosting.user_id == user_id,
            JobPosting.status == "new",
            JobPosting.notified_at.is_(None),
            JobPosting.first_seen_at >= now - MATCH_WINDOW,
            JobScore.score >= min_score,
        )
        .order_by(JobScore.score.desc(), JobPosting.first_seen_at.desc())
        .limit(200)
    )
    return [(p, int(s)) for p, s in rows.all()]


def _pay(p: JobPosting) -> str:
    if p.pay_min_cents is None:
        return ""
    unit = "/hr" if p.pay_period == "hour" else "/yr"
    lo = p.pay_min_cents / 100
    hi = (p.pay_max_cents or p.pay_min_cents) / 100

    def fmt(v: float) -> str:
        return f"${v / 1000:.0f}k" if unit == "/yr" else f"${v:.0f}"

    return f" \u00b7 {fmt(lo)}{'' if hi == lo else '-' + fmt(hi)}{unit}"


async def build_matches(
    db: AsyncSession, row: NotificationSettings, settings: Settings, now: datetime
) -> tuple[Message | None, list[JobPosting]]:
    """New postings that pass the filters and score at or above the threshold.

    Returns the message (None when nothing qualifies) and every posting considered, so the
    caller can mark them all and never look at them again."""
    candidates = await match_candidates(db, row.user_id, row.match_min_score, now)
    if not candidates:
        return None, []
    filters = await db.get(JobFilters, row.user_id)
    picked = [(p, s) for p, s in candidates if evaluate(p, filters).passed][:MAX_MATCHES]
    considered = [p for p, _ in candidates]
    if not picked:
        return None, considered
    lines = [
        f"{s} \u00b7 {p.title}{' at ' + p.company if p.company else ''}"
        f"{' (' + p.location + ')' if p.location else ''}{_pay(p)}"
        for p, s in picked
    ]
    title = f"CareerLine: {len(picked)} new job match{'es' if len(picked) != 1 else ''}"
    return Message(kind="matches", title=title, lines=lines, link=_link(settings, "/jobs")), (
        considered
    )


def test_message(settings: Settings) -> Message:
    return Message(
        kind="test",
        title="CareerLine test notification",
        lines=["Notifications are working. You'll get your digest and job matches here."],
        link=_link(settings, "/settings/notifications"),
    )
