# SPDX-License-Identifier: Apache-2.0
"""Insights API: analytics over the user's own tracker data."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query

from waypoint.auth.sessions import DB, CurrentUser, csrf_protect
from waypoint.insights import service

router = APIRouter(
    prefix="/api/v1/insights", tags=["insights"], dependencies=[Depends(csrf_protect)]
)


@router.get("")
async def insights(
    auth: CurrentUser,
    db: DB,
    days: int | None = Query(default=None, ge=1, le=3650),
    today: date | None = None,
) -> dict[str, Any]:
    """``days`` limits to applications applied (or added) in that many days; omit for all."""
    now = today or datetime.now(UTC).date()
    since = now - timedelta(days=days) if days else None
    facts = await service.load(db, auth.user.id, since)
    return {"days": days, "today": now.isoformat(), **service.summarize(facts, now)}
