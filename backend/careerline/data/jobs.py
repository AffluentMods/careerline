# SPDX-License-Identifier: Apache-2.0
"""Scheduled backups run in the worker."""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from careerline.data import backup
from careerline.discovery.models import QueuedJob
from careerline.worker import queue
from careerline.worker.runner import SCHEDULE_HOOKS, PermanentError, WorkerContext, handler

log = logging.getLogger(__name__)


async def schedule_backup(ctx: WorkerContext, db: AsyncSession) -> int:
    settings = ctx.settings
    if settings.backup_dir is None:
        return 0
    interval = timedelta(hours=settings.backup_interval_hours)
    now = queue.utcnow()
    latest = backup.list_backups(settings.backup_dir)
    if latest and now - latest[0].created_at < interval:
        return 0
    # Don't retry a failing backup every 30 seconds: once per interval is enough.
    recent = await db.scalar(
        select(QueuedJob.id).where(
            QueuedJob.kind == "backup",
            QueuedJob.dedupe_key == "backup",
            QueuedJob.created_at > now - interval,
        )
    )
    if recent is not None:
        return 0
    await queue.enqueue(db, "backup", {}, dedupe_key="backup", max_attempts=2)
    return 1


SCHEDULE_HOOKS.append(schedule_backup)


@handler("backup")
async def run_backup(ctx: WorkerContext, job: QueuedJob) -> None:
    try:
        made = await backup.create_backup(ctx.sessionmaker, ctx.settings)
    except backup.BackupError as exc:
        raise PermanentError(str(exc)) from None
    log.info("backup written: %s (%d bytes)", made.name, made.size)
