# SPDX-License-Identifier: Apache-2.0
"""Worker side of notifications: when to send the digest and match alerts, and sending them."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from careerline.ai.jobs import allow_private_for
from careerline.ai.models import JobScore
from careerline.discovery.models import JobPosting, QueuedJob
from careerline.net import SafeFetcher
from careerline.notify import service
from careerline.notify.models import NotificationSettings
from careerline.worker import queue
from careerline.worker.runner import SCHEDULE_HOOKS, PermanentError, WorkerContext, handler

# Send the digest at the chosen hour, or within this many hours after (worker downtime).
DIGEST_WINDOW_HOURS = 3


async def _fetcher(ctx: WorkerContext, db: AsyncSession, user_id: uuid.UUID) -> SafeFetcher:
    fetcher = ctx.fetcher_factory()
    fetcher.allow_private = await allow_private_for(
        db, user_id, ctx.settings.outbound_allow_private
    )
    return fetcher


async def schedule_notifications(ctx: WorkerContext, db: AsyncSession) -> int:
    if ctx.settings.demo_mode:
        return 0
    queued = 0
    now = queue.utcnow()
    rows = await db.scalars(
        select(NotificationSettings).where(
            or_(NotificationSettings.digest_enabled, NotificationSettings.matches_enabled)
        )
    )
    for row in rows:
        local = service.local_now(row, now)
        hours_in = local.hour - row.digest_hour
        if (
            row.digest_enabled
            and 0 <= hours_in < DIGEST_WINDOW_HOURS
            and row.last_digest_on != local.date()
        ):
            # Mark first: at most one digest a day, even if sending keeps failing.
            row.last_digest_on = local.date()
            await queue.enqueue(
                db,
                "notify_digest",
                {"date": local.date().isoformat()},
                user_id=row.user_id,
                dedupe_key=f"digest:{row.user_id}",
            )
            queued += 1
        if row.matches_enabled and await db.scalar(
            select(
                exists().where(
                    JobPosting.user_id == row.user_id,
                    JobPosting.status == "new",
                    JobPosting.notified_at.is_(None),
                    JobPosting.first_seen_at >= now - service.MATCH_WINDOW,
                    JobScore.user_id == JobPosting.user_id,
                    JobScore.posting_id == JobPosting.id,
                    JobScore.score >= row.match_min_score,
                )
            )
        ):
            await queue.enqueue(
                db, "notify_matches", {}, user_id=row.user_id, dedupe_key=f"matches:{row.user_id}"
            )
            queued += 1
    return queued


SCHEDULE_HOOKS.append(schedule_notifications)


@handler("notify_digest")
async def notify_digest(ctx: WorkerContext, job: QueuedJob) -> None:
    if job.user_id is None:
        raise PermanentError("digest without a user")
    async with ctx.sessionmaker() as db:
        row = await db.get(NotificationSettings, job.user_id)
        if row is None or not row.digest_enabled:
            return
        today = date.fromisoformat(str(job.payload.get("date")))
        msg = await service.build_digest(db, job.user_id, today, ctx.settings)
        if msg is None:
            return
        fetcher = await _fetcher(ctx, db, job.user_id)
        results = await service.deliver(db, row, ctx.cipher, ctx.settings, fetcher, msg)
        await db.commit()
    if results and all(err is not None for err in results.values()):
        raise RuntimeError("every channel failed: " + "; ".join(str(e) for e in results.values()))


@handler("notify_matches")
async def notify_matches(ctx: WorkerContext, job: QueuedJob) -> None:
    if job.user_id is None:
        raise PermanentError("match alert without a user")
    async with ctx.sessionmaker() as db:
        row = await db.get(NotificationSettings, job.user_id)
        if row is None or not row.matches_enabled:
            return
        now = queue.utcnow()
        msg, considered = await service.build_matches(db, row, ctx.settings, now)
        if considered:
            await db.execute(
                update(JobPosting)
                .where(
                    JobPosting.user_id == job.user_id,
                    JobPosting.id.in_([p.id for p in considered]),
                )
                .values(notified_at=now)
            )
        results: dict[str, str | None] = {}
        if msg is not None:
            fetcher = await _fetcher(ctx, db, job.user_id)
            results = await service.deliver(db, row, ctx.cipher, ctx.settings, fetcher, msg)
        await db.commit()
    # Postings stay marked even if delivery failed: an alert is only useful while it's new.
    if results and all(err is not None for err in results.values()):
        raise PermanentError("every channel failed")
