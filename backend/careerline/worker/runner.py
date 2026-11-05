# SPDX-License-Identifier: Apache-2.0
"""The worker loop: schedule due work, then claim and run jobs one at a time."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import socket
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from careerline.config import Settings
from careerline.discovery.adapters import SourceError, run_adapter
from careerline.discovery.models import JobSource, QueuedJob
from careerline.discovery.service import prune_old_postings, store_postings
from careerline.net import SafeFetcher
from careerline.security import contexts
from careerline.security.crypto import FieldCipher
from careerline.worker import queue

log = logging.getLogger("careerline.worker")

SCHEDULE_EVERY = timedelta(seconds=30)
IDLE_SLEEP = 2.0


@dataclass
class WorkerContext:
    sessionmaker: async_sessionmaker[AsyncSession]
    settings: Settings
    cipher: FieldCipher
    fetcher_factory: Callable[[], SafeFetcher]
    worker_id: str = field(default_factory=lambda: f"{socket.gethostname()}:{os.getpid()}")


Handler = Callable[[WorkerContext, QueuedJob], Awaitable[None]]
HANDLERS: dict[str, Handler] = {}


def handler(kind: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn

    return register


class PermanentError(Exception):
    """Fail the job without retrying (the user has to fix something first)."""


# --- scheduling ----------------------------------------------------------------------------


async def schedule_due(ctx: WorkerContext) -> int:
    """Queue fetches for due sources and the daily jobs. Returns how many were queued."""
    queued = 0
    now = queue.utcnow()
    async with ctx.sessionmaker() as db:
        due = await db.scalars(
            select(JobSource)
            .where(JobSource.enabled.is_(True), JobSource.next_run_at <= now)
            .limit(200)
        )
        for source in due:
            source.next_run_at = now + timedelta(hours=source.interval_hours)
            if await queue.enqueue(
                db,
                "fetch_source",
                {"source_id": str(source.id)},
                user_id=source.user_id,
                dedupe_key=f"fetch:{source.id}",
                max_attempts=2,
            ):
                queued += 1
        recent_maintenance = await db.scalar(
            select(QueuedJob.id).where(
                QueuedJob.kind == "maintenance", QueuedJob.created_at > now - timedelta(hours=24)
            )
        )
        if recent_maintenance is None:
            await queue.enqueue(db, "maintenance", {}, dedupe_key="maintenance")
            queued += 1
        for hook in SCHEDULE_HOOKS:
            queued += await hook(ctx, db)
        await db.commit()
    return queued


ScheduleHook = Callable[[WorkerContext, AsyncSession], Awaitable[int]]
SCHEDULE_HOOKS: list[ScheduleHook] = []


# --- handlers ------------------------------------------------------------------------------


def _secret(ctx: WorkerContext, source: JobSource) -> dict[str, object]:
    if not source.secret_enc:
        return {}
    data: dict[str, object] = json.loads(
        ctx.cipher.decrypt(source.secret_enc, contexts.source_secret(source.user_id, source.id))
    )
    return data


@handler("fetch_source")
async def fetch_source(ctx: WorkerContext, job: QueuedJob) -> None:
    source_id = uuid.UUID(str(job.payload["source_id"]))
    async with ctx.sessionmaker() as db:
        source = await db.scalar(
            select(JobSource).where(JobSource.id == source_id, JobSource.user_id == job.user_id)
        )
        if source is None or not source.enabled:
            return
        kind, config, secret = source.kind, dict(source.config), _secret(ctx, source)
    try:
        postings = await run_adapter(ctx.fetcher_factory(), kind, config, secret)
        error = ""
    except SourceError as exc:
        postings, error = [], str(exc)
    async with ctx.sessionmaker() as db:
        source = await db.get(JobSource, source_id)
        if source is None:
            return
        source.last_run_at = queue.utcnow()
        if error:
            source.last_status, source.last_error = "error", error[:300]
            await db.commit()
            raise PermanentError(error)
        stored = await store_postings(db, source.user_id, source, postings)
        source.last_status, source.last_error = "ok", ""
        source.last_found, source.last_new = stored.found, len(stored.new_ids)
        if stored.new_ids:
            await queue.enqueue(
                db,
                "score_postings",
                {},
                user_id=source.user_id,
                dedupe_key=f"score:{source.user_id}",
            )
        await db.commit()
        log.info("source %s: %d found, %d new", source.id, stored.found, len(stored.new_ids))


@handler("maintenance")
async def maintenance(ctx: WorkerContext, job: QueuedJob) -> None:
    from sqlalchemy import delete

    from careerline.models import RateLimitHit

    async with ctx.sessionmaker() as db:
        pruned = await prune_old_postings(db)
        await db.execute(
            delete(QueuedJob).where(
                QueuedJob.status.in_(("done", "failed")),
                QueuedJob.finished_at < queue.utcnow() - timedelta(days=14),
            )
        )
        await db.execute(
            delete(RateLimitHit).where(RateLimitHit.created_at < queue.utcnow() - timedelta(days=2))
        )
        await db.commit()
    log.info("maintenance: pruned %d old postings", pruned)


# --- loop ------------------------------------------------------------------------------------


async def run_one(ctx: WorkerContext) -> bool:
    """Claim and run one job. Returns False when the queue is empty."""
    async with ctx.sessionmaker() as db:
        job = await queue.claim(db, ctx.worker_id)
    if job is None:
        return False
    fn = HANDLERS.get(job.kind)
    async with ctx.sessionmaker() as db:
        if fn is None:
            await queue.fail(db, job, f"unknown job kind {job.kind!r}", retry=False)
            return True
        try:
            await fn(ctx, job)
        except PermanentError as exc:
            await queue.fail(db, job, str(exc), retry=False)
        except Exception as exc:
            log.exception("job %s (%s) failed", job.id, job.kind)
            await queue.fail(db, job, f"{type(exc).__name__}: {exc}")
        else:
            await queue.finish(db, job.id)
    return True


async def drain(ctx: WorkerContext, limit: int = 1000) -> int:
    """Run jobs until the queue is empty (used by tests and the 'run now' path)."""
    count = 0
    while count < limit and await run_one(ctx):
        count += 1
    return count


async def run_forever(ctx: WorkerContext, stop: asyncio.Event) -> None:
    last_schedule = queue.utcnow() - SCHEDULE_EVERY
    while not stop.is_set():
        try:
            if queue.utcnow() - last_schedule >= SCHEDULE_EVERY:
                async with ctx.sessionmaker() as db:
                    await queue.requeue_stale(db)
                await schedule_due(ctx)
                last_schedule = queue.utcnow()
            worked = await run_one(ctx)
        except Exception:
            log.exception("worker loop error")
            worked = False
        if not worked:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=IDLE_SLEEP)
