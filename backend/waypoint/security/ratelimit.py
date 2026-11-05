# SPDX-License-Identifier: Apache-2.0
"""Sliding-window rate limiting stored in Postgres (no Redis needed)."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from waypoint.errors import ApiError
from waypoint.models import RateLimitHit


async def check_and_hit(db: AsyncSession, key: str, limit: int, window: timedelta) -> None:
    """Record a hit for ``key``; raise 429 if the window already holds ``limit`` hits.

    The caller must commit the session so the hit is saved even if the request
    later fails.
    """
    now = datetime.now(UTC)
    count = await db.scalar(
        select(func.count())
        .select_from(RateLimitHit)
        .where(RateLimitHit.key == key, RateLimitHit.created_at > now - window)
    )
    if (count or 0) >= limit:
        raise ApiError(429, "rate_limited", "Too many attempts. Please wait and try again.")
    db.add(RateLimitHit(key=key))
    # Prune old rows now and then rather than running a separate job.
    if secrets.randbelow(100) == 0:
        await db.execute(
            delete(RateLimitHit).where(RateLimitHit.created_at < now - timedelta(days=1))
        )
