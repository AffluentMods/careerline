# SPDX-License-Identifier: Apache-2.0
"""Job queue on Postgres: enqueue, claim with SKIP LOCKED, retry with backoff."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from careerline.discovery.models import QueuedJob

STALE_AFTER = timedelta(minutes=15)


def utcnow() -> datetime:
    return datetime.now(UTC)


async def enqueue(
    db: AsyncSession,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    user_id: uuid.UUID | None = None,
    dedupe_key: str | None = None,
    run_after: datetime | None = None,
    max_attempts: int = 3,
) -> bool:
    """Add a job (or move an identical queued one earlier). Returns False only when an
    identical job is already running. Caller commits."""
    stmt = insert(QueuedJob).values(
        kind=kind,
        payload=payload or {},
        user_id=user_id,
        dedupe_key=dedupe_key,
        run_after=run_after or utcnow(),
        max_attempts=max_attempts,
    )
    if dedupe_key:
        # Same job already queued: keep one, but let the earlier start time win (a fetch
        # shouldn't wait behind a debounced rescore). A running job is left alone.
        stmt = stmt.on_conflict_do_update(
            index_elements=["dedupe_key"],
            index_where=text("status IN ('queued', 'running') AND dedupe_key IS NOT NULL"),
            set_={"run_after": func.least(QueuedJob.run_after, stmt.excluded.run_after)},
            where=QueuedJob.status == "queued",
        )
    result = await db.execute(stmt.returning(QueuedJob.id))
    return result.scalar_one_or_none() is not None


async def claim(db: AsyncSession, worker_id: str) -> QueuedJob | None:
    """Take the next ready job and mark it running. Commits."""
    ready = (
        select(QueuedJob.id)
        .where(QueuedJob.status == "queued", QueuedJob.run_after <= utcnow())
        .order_by(QueuedJob.run_after, QueuedJob.id)
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    result = await db.execute(
        update(QueuedJob)
        .where(QueuedJob.id == ready)
        .values(
            status="running",
            locked_by=worker_id[:100],
            locked_at=utcnow(),
            attempts=QueuedJob.attempts + 1,
        )
        .returning(QueuedJob)
    )
    job = result.scalar_one_or_none()
    await db.commit()
    return job


async def finish(db: AsyncSession, job_id: int) -> None:
    await db.execute(
        update(QueuedJob)
        .where(QueuedJob.id == job_id)
        .values(status="done", finished_at=utcnow(), last_error="")
    )
    await db.commit()


async def fail(db: AsyncSession, job: QueuedJob, error: str, *, retry: bool = True) -> None:
    """Retry with exponential backoff (1, 4, 16 minutes...) until max_attempts."""
    if retry and job.attempts < job.max_attempts:
        delay = timedelta(minutes=4 ** (job.attempts - 1))
        values: dict[str, Any] = {"status": "queued", "run_after": utcnow() + delay}
    else:
        values = {"status": "failed", "finished_at": utcnow()}
    await db.execute(
        update(QueuedJob).where(QueuedJob.id == job.id).values(last_error=error[:500], **values)
    )
    await db.commit()


async def requeue_stale(db: AsyncSession) -> int:
    """Jobs stuck in 'running' (a worker crashed) go back to the queue."""
    result = await db.execute(
        update(QueuedJob)
        .where(QueuedJob.status == "running", QueuedJob.locked_at < utcnow() - STALE_AFTER)
        .values(status="queued", run_after=utcnow())
    )
    await db.commit()
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def is_active(db: AsyncSession, dedupe_key: str) -> bool:
    found = await db.scalar(
        select(QueuedJob.id).where(
            QueuedJob.dedupe_key == dedupe_key, QueuedJob.status.in_(("queued", "running"))
        )
    )
    return found is not None
