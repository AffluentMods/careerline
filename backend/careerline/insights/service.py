# SPDX-License-Identifier: Apache-2.0
"""Search analytics from the tracker: stage funnel, response rate by source, response time.

Definitions (also shown in the UI):
- Applied: the application has an applied date or reached any stage from Applied on.
- Response: the employer answered, i.e. the application reached Screening, Interviewing,
  Offer, Accepted, or Rejected. Withdrawing yourself is not a response.
- Positive response: Screening or further.
- Time to response: days from the applied date to the first stage change that counts as
  a response. Applications created directly at a later stage have no measurable time.
"""

from __future__ import annotations

import statistics
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from careerline.tracker.models import Application, ApplicationEvent

FUNNEL = ("applied", "screening", "interviewing", "offer", "accepted")
RESPONSE = frozenset({"screening", "interviewing", "offer", "accepted", "rejected"})
POSITIVE = frozenset({"screening", "interviewing", "offer", "accepted"})
# Applied this long ago with no answer: counted as "no response yet" (ghosted).
QUIET_DAYS = 30
WEEKS = 12
NOT_SET = "Not set"


@dataclass
class AppFacts:
    source: str
    stage: str
    applied_on: date | None
    reached: set[str] = field(default_factory=set)
    first_response: date | None = None

    @property
    def furthest(self) -> int:
        """Index into FUNNEL of the furthest stage reached, or -1."""
        return max((FUNNEL.index(s) for s in self.reached if s in FUNNEL), default=-1)

    @property
    def applied(self) -> bool:
        return self.applied_on is not None or self.furthest >= 0

    @property
    def responded(self) -> bool:
        return self.applied and bool(self.reached & RESPONSE)

    @property
    def positive(self) -> bool:
        return self.applied and bool(self.reached & POSITIVE)

    @property
    def days_to_response(self) -> int | None:
        if self.applied_on and self.first_response and self.first_response >= self.applied_on:
            return (self.first_response - self.applied_on).days
        return None


async def load(db: AsyncSession, user_id: uuid.UUID, since: date | None) -> list[AppFacts]:
    stmt = select(Application).where(Application.user_id == user_id)
    if since is not None:
        start = datetime.combine(since, time.min, tzinfo=UTC)
        stmt = stmt.where(
            or_(
                Application.applied_on >= since,
                and_(Application.applied_on.is_(None), Application.created_at >= start),
            )
        )
    apps = {a.id: a for a in await db.scalars(stmt)}
    facts = {
        a.id: AppFacts(
            source=(a.source or "").strip() or NOT_SET,
            stage=a.stage,
            applied_on=a.applied_on,
            reached={a.stage},
        )
        for a in apps.values()
    }
    if not apps:
        return []
    events = await db.execute(
        select(ApplicationEvent.application_id, ApplicationEvent.occurred_at, ApplicationEvent.data)
        .where(
            ApplicationEvent.user_id == user_id,
            ApplicationEvent.kind == "stage_change",
            ApplicationEvent.application_id.in_(list(apps)),
        )
        .order_by(ApplicationEvent.occurred_at)
    )
    for app_id, occurred_at, data in events.all():
        f = facts[app_id]
        to = data.get("to") if isinstance(data, dict) else None
        if not isinstance(to, str):
            continue
        f.reached.add(to)
        if to in RESPONSE and f.first_response is None:
            f.first_response = occurred_at.date()
    return list(facts.values())


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 3) if whole else None


def _median(values: list[int]) -> float | None:
    return round(float(statistics.median(values)), 1) if values else None


def summarize(facts: list[AppFacts], today: date) -> dict[str, object]:
    applied = [f for f in facts if f.applied]
    responded = [f for f in applied if f.responded]
    days = [d for f in applied if (d := f.days_to_response) is not None]
    quiet = [
        f
        for f in applied
        if not f.responded
        and f.stage == "applied"
        and f.applied_on is not None
        and (today - f.applied_on).days >= QUIET_DAYS
    ]

    funnel = []
    previous: int | None = None
    for i, stage in enumerate(FUNNEL):
        count = sum(1 for f in applied if f.furthest >= i)
        funnel.append(
            {
                "stage": stage,
                "count": count,
                "rate_from_previous": _rate(count, previous) if previous is not None else None,
                "rate_from_applied": _rate(count, len(applied)),
            }
        )
        previous = count

    by_source: dict[str, list[AppFacts]] = defaultdict(list)
    for f in applied:
        by_source[f.source].append(f)
    sources = []
    for name, group in by_source.items():
        group_days = [d for f in group if (d := f.days_to_response) is not None]
        resp = sum(f.responded for f in group)
        sources.append(
            {
                "source": name,
                "applied": len(group),
                "responded": resp,
                "positive": sum(f.positive for f in group),
                "offers": sum(f.furthest >= FUNNEL.index("offer") for f in group),
                "response_rate": _rate(resp, len(group)),
                "median_days_to_response": _median(group_days),
            }
        )
    sources.sort(key=lambda s: (-len(by_source[str(s["source"])]), str(s["source"]).lower()))

    week0 = today - timedelta(days=today.weekday()) - timedelta(weeks=WEEKS - 1)
    weekly = {week0 + timedelta(weeks=i): 0 for i in range(WEEKS)}
    for f in applied:
        if f.applied_on and f.applied_on >= week0:
            start = f.applied_on - timedelta(days=f.applied_on.weekday())
            if start in weekly:
                weekly[start] += 1

    buckets = {"Within a week": 0, "1\u20132 weeks": 0, "2\u20134 weeks": 0, "Over 4 weeks": 0}
    for d in days:
        key = (
            "Within a week"
            if d < 7
            else "1\u20132 weeks"
            if d < 14
            else "2\u20134 weeks"
            if d < 28
            else "Over 4 weeks"
        )
        buckets[key] += 1

    return {
        "totals": {
            "tracked": len(facts),
            "applied": len(applied),
            "responded": len(responded),
            "positive": sum(f.positive for f in applied),
            "interviews": sum(f.furthest >= FUNNEL.index("interviewing") for f in applied),
            "offers": sum(f.furthest >= FUNNEL.index("offer") for f in applied),
            "rejected": sum(f.stage == "rejected" for f in applied),
            "withdrawn": sum(f.stage == "withdrawn" for f in applied),
            "awaiting": sum(
                f.stage == "applied" and not f.responded and f not in quiet for f in applied
            ),
            "quiet": len(quiet),
        },
        "response_rate": _rate(len(responded), len(applied)),
        "positive_rate": _rate(sum(f.positive for f in applied), len(applied)),
        "median_days_to_response": _median(days),
        "measured_responses": len(days),
        "funnel": funnel,
        "sources": sources,
        "weekly": [{"week_start": k.isoformat(), "applied": v} for k, v in weekly.items()],
        "response_times": [{"bucket": k, "count": v} for k, v in buckets.items()],
        "quiet_days": QUIET_DAYS,
    }
