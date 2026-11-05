# SPDX-License-Identifier: Apache-2.0
"""Storing postings (with de-duplication) and evaluating the user's hard filters."""

from __future__ import annotations

import math
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from waypoint.discovery.models import JobFilters, JobPosting, JobSource, PostingSighting
from waypoint.discovery.normalize import (
    NormalizedPosting,
    clearance_rank,
    fingerprint,
    hourly_cents,
)
from waypoint.tracker.models import Application, ApplicationEvent

RETENTION = timedelta(days=60)

CLEARANCE_LABEL = {"public_trust": "Public Trust", "secret": "Secret", "top_secret": "Top Secret"}
SHIFT_LABEL = {
    "day": "day shift",
    "early": "early shift",
    "evening": "evening shift",
    "night": "night shift",
    "weekend": "weekend work",
    "rotating": "rotating shifts",
    "on_call": "on-call",
}
MODE_LABEL = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "On-site"}


def utcnow() -> datetime:
    return datetime.now(UTC)


# --- storing ------------------------------------------------------------------------------


@dataclass
class StoreResult:
    found: int = 0
    new_ids: list[uuid.UUID] = field(default_factory=list)


async def store_postings(
    db: AsyncSession,
    user_id: uuid.UUID,
    source: JobSource | None,
    postings: Sequence[NormalizedPosting],
    source_label: str = "",
) -> StoreResult:
    """Insert new postings, merge duplicates by fingerprint, record sightings. Caller commits."""
    result = StoreResult(found=len(postings))
    now = utcnow()
    by_fp: dict[str, JobPosting] = {
        p.fingerprint: p
        for p in await db.scalars(select(JobPosting).where(JobPosting.user_id == user_id))
    }
    source_id = source.id if source else None
    sightings: dict[str, PostingSighting] = {
        s.external_id: s
        for s in await db.scalars(
            select(PostingSighting).where(
                PostingSighting.user_id == user_id,
                PostingSighting.source_id.is_(None)
                if source_id is None
                else PostingSighting.source_id == source_id,
            )
        )
    }
    by_id = {p.id: p for p in by_fp.values()}
    label = source.name if source else source_label

    for np in postings:
        fp = fingerprint(np.company, np.title, np.location)
        seen = sightings.get(np.external_id)
        posting = by_id.get(seen.posting_id) if seen else by_fp.get(fp)
        if posting is None:
            posting = JobPosting(
                id=uuid.uuid4(),
                user_id=user_id,
                fingerprint=fp,
                title=np.title,
                company=np.company,
                location=np.location,
                work_mode=np.work_mode,
                pay_min_cents=np.pay_min_cents,
                pay_max_cents=np.pay_max_cents,
                pay_period=np.pay_period,
                currency=np.currency[:3].upper() or "USD",
                shifts=np.shifts,
                clearance=np.clearance,
                employment_type=np.employment_type,
                description=np.description,
                url=np.url,
                posted_at=np.posted_at,
                closes_on=np.closes_on,
                lat=np.lat,
                lon=np.lon,
                status="new",
                first_seen_at=now,
                last_seen_at=now,
            )
            db.add(posting)
            by_fp[fp] = posting
            by_id[posting.id] = posting
            result.new_ids.append(posting.id)
        else:
            posting.last_seen_at = now
            # Fill gaps from the newer sighting; never overwrite what's there.
            if posting.pay_min_cents is None and posting.pay_max_cents is None and np.pay_period:
                posting.pay_min_cents, posting.pay_max_cents = np.pay_min_cents, np.pay_max_cents
                posting.pay_period, posting.currency = (
                    np.pay_period,
                    np.currency[:3].upper() or "USD",
                )
            if len(np.description) > len(posting.description):
                posting.description = np.description
            posting.url = posting.url or np.url
            posting.work_mode = posting.work_mode or np.work_mode
            posting.clearance = posting.clearance or np.clearance
            posting.shifts = posting.shifts or np.shifts
            posting.posted_at = posting.posted_at or np.posted_at
            posting.closes_on = posting.closes_on or np.closes_on
            if posting.lat is None and np.lat is not None:
                posting.lat, posting.lon = np.lat, np.lon
        if seen is None:
            seen = PostingSighting(
                user_id=user_id,
                posting_id=posting.id,
                source_id=source_id,
                source_label=label[:100],
                external_id=np.external_id[:300],
                url=np.url,
                seen_at=now,
            )
            db.add(seen)
            sightings[np.external_id] = seen
        else:
            seen.seen_at = now
            seen.url = np.url or seen.url
    await db.flush()
    return result


async def track(db: AsyncSession, p: JobPosting) -> Application:
    """Create a tracker application (stage Saved) from a posting. Caller commits."""
    if p.application_id is not None:
        existing = await db.scalar(
            select(Application).where(
                Application.user_id == p.user_id, Application.id == p.application_id
            )
        )
        if existing is not None:
            return existing
    first = await db.scalar(
        select(PostingSighting.source_label)
        .where(PostingSighting.user_id == p.user_id, PostingSighting.posting_id == p.id)
        .order_by(PostingSighting.seen_at)
        .limit(1)
    )
    has_pay = p.pay_period is not None and (
        p.pay_min_cents is not None or p.pay_max_cents is not None
    )
    app = Application(
        id=uuid.uuid4(),
        user_id=p.user_id,
        title=p.title,
        company=p.company,
        location=p.location,
        work_mode=p.work_mode,
        job_url=p.url if p.url.startswith("https://") else "",
        source=(first or "Discovery")[:100],
        pay_min_cents=p.pay_min_cents if has_pay else None,
        pay_max_cents=p.pay_max_cents if has_pay else None,
        pay_period=p.pay_period if has_pay else None,
        currency=p.currency,
        description=p.description,
        deadline=p.closes_on,
        stage="saved",
    )
    db.add(app)
    await db.flush()
    db.add(
        ApplicationEvent(
            user_id=p.user_id,
            application_id=app.id,
            kind="stage_change",
            occurred_at=utcnow(),
            title="Added as Saved",
            data={"from": None, "to": "saved", "posting_id": str(p.id)},
        )
    )
    p.application_id = app.id
    p.status = "tracked"
    return app


async def prune_old_postings(db: AsyncSession) -> int:
    """Drop untouched postings nobody has seen for a while. Saved and tracked ones stay."""
    result = await db.execute(
        delete(JobPosting).where(
            JobPosting.last_seen_at < utcnow() - RETENTION,
            JobPosting.status.in_(("new", "dismissed")),
            JobPosting.application_id.is_(None),
        )
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


# --- filters -------------------------------------------------------------------------------


@dataclass
class FilterResult:
    passed: bool
    reasons: list[str]
    preferred: bool
    pay_vs_target: str | None  # above / within / below / None (unknown)
    distance_miles: float | None


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _mentions(haystack: str, phrase: str) -> bool:
    needle = _norm(phrase)
    return bool(needle) and f" {needle} " in f" {haystack} "


def _company_in(company: str, names: Sequence[str]) -> bool:
    c = _norm(company)
    return bool(c) and any(_norm(n) and (_norm(n) == c or _norm(n) in c) for n in names)


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}".replace(".00", "")


def evaluate(p: JobPosting, f: JobFilters | None) -> FilterResult:
    """Apply hard filters. Unknown values (no pay posted, no location) never fail a filter
    unless the user asked for that explicitly (hide_unpaid_postings)."""
    reasons: list[str] = []
    hourly_lo = hourly_cents(p.pay_min_cents, p.pay_period) if p.pay_min_cents is not None else None
    hourly_hi = hourly_cents(p.pay_max_cents, p.pay_period) if p.pay_max_cents is not None else None
    top = hourly_hi if hourly_hi is not None else hourly_lo
    bottom = hourly_lo if hourly_lo is not None else hourly_hi
    if f is None:
        return FilterResult(True, [], False, None, None)
    text = _norm(f"{p.title} {p.description}")

    if _company_in(p.company, f.blocked_companies):
        reasons.append("The company is on your blocked list.")
    for word in f.exclude_keywords:
        if _mentions(text, word):
            reasons.append(f'Mentions "{word}".')
            break
    if f.require_keywords and not any(_mentions(text, w) for w in f.require_keywords):
        reasons.append("Doesn't mention any of: " + ", ".join(f.require_keywords) + ".")

    mode_ok = {"remote": f.remote_ok, "hybrid": f.hybrid_ok, "onsite": f.onsite_ok}
    if p.work_mode and not mode_ok.get(p.work_mode, True):
        reasons.append(f"{MODE_LABEL[p.work_mode]} roles are turned off.")

    distance = None
    if p.work_mode != "remote":
        if None not in (f.home_lat, f.home_lon, p.lat, p.lon):
            distance = haversine_miles(f.home_lat, f.home_lon, p.lat, p.lon)  # type: ignore[arg-type]
        if f.radius_miles and distance is not None and distance > f.radius_miles:
            reasons.append(f"About {round(distance)} miles away (your limit is {f.radius_miles}).")
        elif (
            distance is None
            and f.locations
            and p.location
            and not any(_mentions(_norm(p.location), loc) for loc in f.locations)
        ):
            reasons.append("Not in one of your locations.")

    if f.min_hourly_cents:
        if top is not None and top < f.min_hourly_cents:
            reasons.append(
                f"Pays up to {_money(top)}/hr, below your {_money(f.min_hourly_cents)}/hr minimum."
            )
        elif top is None and f.hide_unpaid_postings:
            reasons.append("No pay range is posted.")

    excluded = [s for s in p.shifts if s in f.shifts_excluded]
    if excluded:
        reasons.append(f"Mentions {SHIFT_LABEL.get(excluded[0], excluded[0])}.")
    elif f.shifts_wanted and p.shifts and not set(p.shifts) & set(f.shifts_wanted):
        reasons.append("The schedule doesn't match the shifts you want.")

    if clearance_rank(p.clearance) > clearance_rank(f.clearance_held):
        reasons.append(f"Requires {CLEARANCE_LABEL.get(p.clearance or '', 'a')} clearance.")

    pay_vs_target = None
    if f.target_hourly_cents and top is not None and bottom is not None:
        if top < f.target_hourly_cents:
            pay_vs_target = "below"
        elif bottom >= f.target_hourly_cents:
            pay_vs_target = "above"
        else:
            pay_vs_target = "within"

    return FilterResult(
        passed=not reasons,
        reasons=reasons,
        preferred=_company_in(p.company, f.preferred_companies),
        pay_vs_target=pay_vs_target,
        distance_miles=round(distance, 1) if distance is not None else None,
    )
