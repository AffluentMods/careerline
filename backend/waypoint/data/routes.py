# SPDX-License-Identifier: Apache-2.0
"""Data API: full export, import, and (for the owner) backup status."""

from __future__ import annotations

import contextlib
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from starlette.background import BackgroundTask

from waypoint import audit
from waypoint.auth.sessions import (
    DB,
    CipherDep,
    CurrentUser,
    SettingsDep,
    csrf_protect,
    require_reauth,
)
from waypoint.data import backup, portable
from waypoint.discovery.models import QueuedJob
from waypoint.errors import ApiError
from waypoint.security.ratelimit import check_and_hit
from waypoint.worker import queue

router = APIRouter(prefix="/api/v1/data", tags=["data"], dependencies=[Depends(csrf_protect)])
IMPORT_PATH = "/api/v1/data/import"


@router.post("/export")
async def export(request: Request, auth: CurrentUser, db: DB, cipher: CipherDep) -> FileResponse:
    require_reauth(auth)
    uid = auth.user.id
    await check_and_hit(db, f"export:{uid}", 10, timedelta(hours=1))
    path, counts = await portable.export_user(db, cipher, auth.user)
    audit.record(db, request, "data.exported", uid, records=sum(counts.values()))
    await db.commit()
    stamp = datetime.now().strftime("%Y-%m-%d")
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"waypoint-export-{stamp}.zip",
        background=BackgroundTask(portable.discard, path),
        headers={"Cache-Control": "no-store"},
    )


class ImportOut(BaseModel):
    imported: dict[str, int]
    notes: list[str]
    from_version: str | None


@router.post(IMPORT_PATH.removeprefix("/api/v1/data"))
async def import_archive(
    request: Request, auth: CurrentUser, db: DB, cipher: CipherDep, settings: SettingsDep
) -> ImportOut:
    require_reauth(auth)
    uid = auth.user.id
    await check_and_hit(db, f"import:{uid}", 5, timedelta(hours=1))
    await db.commit()
    fd, name = tempfile.mkstemp(prefix="waypoint-import-", suffix=".zip")
    handle = os.fdopen(fd, "wb")
    path = Path(name)
    try:
        size = 0
        with handle:
            async for chunk in request.stream():
                size += len(chunk)
                if size > settings.import_archive_max_bytes:
                    raise ApiError(413, "too_large", "That file is too large to import.")
                handle.write(chunk)
        if size == 0:
            raise ApiError(422, "empty", "Choose a Waypoint export (.zip) to import.")
        try:
            result: dict[str, Any] = await portable.import_user(db, cipher, settings, uid, path)
        except portable.ImportProblem as exc:
            await db.rollback()
            raise ApiError(422, "import_failed", str(exc)) from None
        except DBAPIError:
            await db.rollback()
            raise ApiError(
                422, "import_failed", "The export has data this server can't store."
            ) from None
        audit.record(db, request, "data.imported", uid, records=sum(result["imported"].values()))
        await db.commit()
        return ImportOut(**result)
    finally:
        with contextlib.suppress(OSError):
            portable.discard(path)


class BackupFileOut(BaseModel):
    name: str
    size: int
    created_at: datetime


class BackupStatus(BaseModel):
    enabled: bool
    directory: str
    interval_hours: int
    keep: int
    key_source: str
    files: list[BackupFileOut]
    last_job_status: str | None
    last_job_error: str
    last_job_at: datetime | None


def _owner(auth: CurrentUser) -> None:
    if not auth.user.is_owner:
        raise ApiError(403, "owner_only", "Only the server owner can manage backups.")


@router.get("/backups")
async def backup_status(auth: CurrentUser, db: DB, settings: SettingsDep) -> BackupStatus:
    _owner(auth)
    job = await db.scalar(
        select(QueuedJob).where(QueuedJob.kind == "backup").order_by(QueuedJob.created_at.desc())
    )
    directory = settings.backup_dir
    return BackupStatus(
        enabled=directory is not None,
        directory=str(directory) if directory else "",
        interval_hours=settings.backup_interval_hours,
        keep=settings.backup_keep,
        key_source="WAYPOINT_BACKUP_KEY" if settings.backup_key else "WAYPOINT_ENCRYPTION_KEYS",
        files=[
            BackupFileOut(name=f.name, size=f.size, created_at=f.created_at)
            for f in (backup.list_backups(directory) if directory else [])
        ],
        last_job_status=job.status if job else None,
        last_job_error=(job.last_error or "") if job else "",
        last_job_at=(job.finished_at or job.created_at) if job else None,
    )


@router.post("/backups", status_code=202)
async def backup_now(
    request: Request, auth: CurrentUser, db: DB, settings: SettingsDep
) -> dict[str, bool]:
    _owner(auth)
    if settings.backup_dir is None:
        raise ApiError(409, "backups_off", "Backups are off. Set WAYPOINT_BACKUP_DIR first.")
    await check_and_hit(db, f"backup-now:{auth.user.id}", 6, timedelta(hours=1))
    queued = await queue.enqueue(db, "backup", {}, dedupe_key="backup", max_attempts=2)
    audit.record(db, request, "data.backup_requested", auth.user.id)
    await db.commit()
    return {"queued": queued}
