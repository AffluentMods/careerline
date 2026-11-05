# SPDX-License-Identifier: Apache-2.0
"""Assistant tools over the user's own data.

Read tools run immediately and return JSON. Write tools never change anything when the
model calls them: they create a ``PendingAction`` that the user confirms in the app.
There are no tools that send email or messages or reach outside the app.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from waypoint.ai.facts import load_facts
from waypoint.ai.prompting import strict_schema, untrusted
from waypoint.ai.providers import ToolSpec
from waypoint.ai.scoring import scores_for, tokens
from waypoint.discovery.models import JobFilters, JobPosting
from waypoint.discovery.normalize import hourly_cents
from waypoint.discovery.service import evaluate, track
from waypoint.tracker import service as tracker
from waypoint.tracker.models import (
    Application,
    ApplicationContact,
    ApplicationEvent,
    Contact,
    Offer,
    Reminder,
)

Stage = Literal[
    "saved", "applied", "screening", "interviewing", "offer", "accepted", "rejected", "withdrawn"
]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListDue(ToolInput):
    days: int = Field(
        ge=0, le=60, description="How many days ahead to look (overdue items are always included)."
    )


class ListApplications(ToolInput):
    stage: Stage | None = Field(description="Only this stage, or null for all.")


class GetApplication(ToolInput):
    application_id: str = Field(description="The application's id.")


class SearchJobs(ToolInput):
    query: str = Field(
        max_length=200,
        description="Words to match in title, company, or description. Empty for all.",
    )
    min_hourly_dollars: float | None = Field(
        description="Only jobs whose posted pay reaches this hourly rate, or null."
    )
    location: str | None = Field(description="Words to match in the location, or null.")
    remote_only: bool = Field(description="Only remote jobs.")
    include_filtered: bool = Field(description="Also include jobs the user's hard filters hide.")


class GetJob(ToolInput):
    posting_id: str = Field(description="The job posting's id.")


class GetVaultFacts(ToolInput):
    pass


class CreateReminder(ToolInput):
    title: Title
    due_on: date = Field(description="Date in YYYY-MM-DD.")
    kind: Literal["follow_up", "deadline", "interview", "task"]
    application_id: str | None = Field(description="Related application id, or null.")


class MoveApplication(ToolInput):
    application_id: str
    stage: Stage
    note: str = Field(
        max_length=2000, description="Optional note for the timeline; empty string for none."
    )


class AddNote(ToolInput):
    application_id: str
    text: Text


class SaveJob(ToolInput):
    posting_id: str


class TrackJob(ToolInput):
    posting_id: str


class ToolError(Exception):
    """A problem to report back to the model as a tool error."""


def _uuid(value: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise ToolError(f"{what} {value!r} is not a valid id.") from None


async def _app(db: AsyncSession, uid: uuid.UUID, app_id: str) -> Application:
    app = await db.scalar(
        select(Application).where(
            Application.user_id == uid, Application.id == _uuid(app_id, "Application")
        )
    )
    if app is None:
        raise ToolError(f"No application with id {app_id}.")
    return app


async def _posting(db: AsyncSession, uid: uuid.UUID, posting_id: str) -> JobPosting:
    p = await db.scalar(
        select(JobPosting).where(
            JobPosting.user_id == uid, JobPosting.id == _uuid(posting_id, "Job")
        )
    )
    if p is None:
        raise ToolError(f"No job posting with id {posting_id}.")
    return p


def _pay(lo: int | None, hi: int | None, period: str | None, currency: str) -> str:
    low = lo if lo is not None else hi
    high = hi if hi is not None else lo
    if low is None or high is None:
        return ""
    unit = "hour" if period == "hour" else "year"
    if low == high:
        return f"{currency} {low / 100:,.0f} per {unit}"
    return f"{currency} {low / 100:,.0f}-{high / 100:,.0f} per {unit}"


# --- read tools ------------------------------------------------------------------------


async def list_due(db: AsyncSession, uid: uuid.UUID, args: ListDue, today: date) -> Any:
    items = await tracker.agenda_items(db, uid, until=today + timedelta(days=args.days))
    return [
        {
            "due_on": i.due_on.isoformat(),
            "time": i.due_time.isoformat() if i.due_time else None,
            "what": i.title,
            "kind": i.kind,
            "application": i.application_title,
            "company": i.company,
            "application_id": str(i.application_id) if i.application_id else None,
            "overdue": i.due_on < today,
        }
        for i in items
    ]


async def list_applications(
    db: AsyncSession, uid: uuid.UUID, args: ListApplications, today: date
) -> Any:
    stmt = (
        select(Application)
        .where(Application.user_id == uid)
        .order_by(Application.updated_at.desc())
        .limit(50)
    )
    if args.stage:
        stmt = stmt.where(Application.stage == args.stage)
    apps = list(await db.scalars(stmt))
    summaries = await tracker.summaries(db, uid, apps)
    return [
        {
            "id": str(s.id),
            "title": s.title,
            "company": s.company,
            "stage": s.stage,
            "applied_on": s.applied_on.isoformat() if s.applied_on else None,
            "days_in_stage": (datetime.now(UTC) - s.stage_changed_at).days,
            "next_due": {"on": s.next_due.due_on.isoformat(), "what": s.next_due.title}
            if s.next_due
            else None,
        }
        for s in summaries
    ]


async def get_application(
    db: AsyncSession, uid: uuid.UUID, args: GetApplication, today: date
) -> Any:
    app = await _app(db, uid, args.application_id)
    events = await db.scalars(
        select(ApplicationEvent)
        .where(ApplicationEvent.user_id == uid, ApplicationEvent.application_id == app.id)
        .order_by(ApplicationEvent.occurred_at.desc())
        .limit(10)
    )
    people = await db.execute(
        select(Contact.name, Contact.title, ApplicationContact.role)
        .join(
            ApplicationContact,
            (ApplicationContact.contact_id == Contact.id)
            & (ApplicationContact.user_id == Contact.user_id),
        )
        .where(ApplicationContact.user_id == uid, ApplicationContact.application_id == app.id)
    )
    reminders = await db.scalars(
        select(Reminder).where(
            Reminder.user_id == uid, Reminder.application_id == app.id, Reminder.done_at.is_(None)
        )
    )
    offer = await db.scalar(
        select(Offer).where(Offer.user_id == uid, Offer.application_id == app.id)
    )
    return {
        "id": str(app.id),
        "title": app.title,
        "company": app.company,
        "location": app.location,
        "stage": app.stage,
        "applied_on": app.applied_on.isoformat() if app.applied_on else None,
        "pay": _pay(app.pay_min_cents, app.pay_max_cents, app.pay_period, app.currency),
        "timeline": [
            {
                "when": e.occurred_at.date().isoformat(),
                "kind": e.kind,
                "title": e.title,
                "details": e.body[:500],
            }
            for e in events
        ],
        # Names and roles only: contact emails and phone numbers are never given to the model.
        "people": [{"name": n, "title": t, "role": r} for n, t, r in people.all()],
        "open_reminders": [{"due_on": r.due_on.isoformat(), "what": r.title} for r in reminders],
        "offer": {
            "base": _pay(offer.base_cents, offer.base_cents, offer.base_period, offer.currency),
            "deadline": offer.deadline.isoformat() if offer.deadline else None,
        }
        if offer
        else None,
        "job_description": untrusted("job_posting", app.description[:6000])
        if app.description
        else "",
    }


async def search_jobs(db: AsyncSession, uid: uuid.UUID, args: SearchJobs, today: date) -> Any:
    postings = list(
        await db.scalars(
            select(JobPosting)
            .where(JobPosting.user_id == uid, JobPosting.status != "dismissed")
            .order_by(JobPosting.first_seen_at.desc())
            .limit(2000)
        )
    )
    filters = await db.get(JobFilters, uid)
    wanted = tokens(args.query) if args.query.strip() else set()
    loc = args.location.lower().strip() if args.location else ""
    hits = []
    for p in postings:
        if wanted and not wanted <= tokens(f"{p.title} {p.company} {p.description}"):
            continue
        if (
            loc
            and loc not in p.location.lower()
            and not (p.work_mode == "remote" and "remote" in loc)
        ):
            continue
        if args.remote_only and p.work_mode != "remote":
            continue
        if args.min_hourly_dollars is not None:
            top = p.pay_max_cents or p.pay_min_cents
            if top is None or hourly_cents(top, p.pay_period) < args.min_hourly_dollars * 100:
                continue
        verdict = evaluate(p, filters)
        if not verdict.passed and not args.include_filtered:
            continue
        hits.append((p, verdict))
    scores = await scores_for(db, uid, [p.id for p, _ in hits[:60]])
    hits.sort(key=lambda pv: -(scores[pv[0].id].score if pv[0].id in scores else -1))
    return [
        {
            "id": str(p.id),
            "title": p.title,
            "company": p.company,
            "location": p.location,
            "work_mode": p.work_mode,
            "pay": _pay(p.pay_min_cents, p.pay_max_cents, p.pay_period, p.currency),
            "fit_score": scores[p.id].score if p.id in scores else None,
            "status": p.status,
            "filtered_out_because": v.reasons or None,
        }
        for p, v in hits[:15]
    ]


async def get_job(db: AsyncSession, uid: uuid.UUID, args: GetJob, today: date) -> Any:
    p = await _posting(db, uid, args.posting_id)
    return {
        "id": str(p.id),
        "title": p.title,
        "company": p.company,
        "location": p.location,
        "work_mode": p.work_mode,
        "pay": _pay(p.pay_min_cents, p.pay_max_cents, p.pay_period, p.currency),
        "status": p.status,
        "application_id": str(p.application_id) if p.application_id else None,
        "description": untrusted("job_posting", p.description[:8000]),
    }


async def get_vault_facts(
    db: AsyncSession, uid: uuid.UUID, args: GetVaultFacts, today: date
) -> Any:
    facts = await load_facts(db, uid)
    return {
        "verified_facts": [f.for_prompt() for f in facts.facts],
        "stories": [{"id": s.id, "title": s.title, "story": s.text} for s in facts.stories],
    }


# --- write tools (proposals) ---------------------------------------------------------------


async def describe_reminder(db: AsyncSession, uid: uuid.UUID, a: CreateReminder) -> str:
    where = f" for {(await _app(db, uid, a.application_id)).title}" if a.application_id else ""
    return f"Add a reminder{where}: “{a.title}” on {a.due_on:%b} {a.due_on.day}, {a.due_on.year}."


async def apply_reminder(db: AsyncSession, uid: uuid.UUID, a: CreateReminder) -> str:
    app_id = (await _app(db, uid, a.application_id)).id if a.application_id else None
    db.add(
        Reminder(user_id=uid, title=a.title, kind=a.kind, due_on=a.due_on, application_id=app_id)
    )
    return "Reminder added."


async def describe_move(db: AsyncSession, uid: uuid.UUID, a: MoveApplication) -> str:
    app = await _app(db, uid, a.application_id)
    at = f" at {app.company}" if app.company else ""
    return f"Move “{app.title}”{at} from {app.stage.capitalize()} to {a.stage.capitalize()}."


async def apply_move(db: AsyncSession, uid: uuid.UUID, a: MoveApplication) -> str:
    app = await _app(db, uid, a.application_id)
    changed = tracker.move_stage(db, app, a.stage, a.note.strip(), datetime.now(UTC).date())
    return f"Moved to {a.stage.capitalize()}." if changed else f"Already in {a.stage.capitalize()}."


async def describe_note(db: AsyncSession, uid: uuid.UUID, a: AddNote) -> str:
    app = await _app(db, uid, a.application_id)
    preview = a.text if len(a.text) <= 120 else a.text[:117] + "..."
    return f"Add a note to “{app.title}”: {preview}"


async def apply_note(db: AsyncSession, uid: uuid.UUID, a: AddNote) -> str:
    app = await _app(db, uid, a.application_id)
    db.add(
        ApplicationEvent(
            user_id=uid,
            application_id=app.id,
            kind="note",
            occurred_at=datetime.now(UTC),
            title="Note from the assistant",
            body=a.text,
        )
    )
    return "Note added."


async def describe_save(db: AsyncSession, uid: uuid.UUID, a: SaveJob) -> str:
    p = await _posting(db, uid, a.posting_id)
    return f"Save the job “{p.title}”{f' at {p.company}' if p.company else ''}."


async def apply_save(db: AsyncSession, uid: uuid.UUID, a: SaveJob) -> str:
    p = await _posting(db, uid, a.posting_id)
    if p.status in ("new", "dismissed"):
        p.status = "saved"
    return "Saved."


async def describe_track(db: AsyncSession, uid: uuid.UUID, a: TrackJob) -> str:
    p = await _posting(db, uid, a.posting_id)
    return f"Add “{p.title}”{f' at {p.company}' if p.company else ''} to the tracker as Saved."


async def apply_track(db: AsyncSession, uid: uuid.UUID, a: TrackJob) -> str:
    p = await _posting(db, uid, a.posting_id)
    app = await track(db, p)
    return f"Added to the tracker (application {app.id})."


# --- registry ------------------------------------------------------------------------------

Reader = Callable[[AsyncSession, uuid.UUID, Any, date], Awaitable[Any]]
Describer = Callable[[AsyncSession, uuid.UUID, Any], Awaitable[str]]
Applier = Callable[[AsyncSession, uuid.UUID, Any], Awaitable[str]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    model: type[ToolInput]
    read: Reader | None = None
    describe: Describer | None = None
    apply: Applier | None = None

    @property
    def writes(self) -> bool:
        return self.apply is not None

    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, strict_schema(self.model))

    def parse(self, raw: dict[str, Any]) -> Any:
        try:
            return self.model.model_validate(raw)
        except ValidationError as exc:
            raise ToolError(f"Invalid arguments: {exc.errors()[0]['msg']}") from None


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in [
        Tool(
            "list_due",
            "List overdue and upcoming reminders, deadlines, and follow-ups.",
            ListDue,
            read=list_due,
        ),
        Tool(
            "list_applications",
            "List the user's tracked applications, newest activity first.",
            ListApplications,
            read=list_applications,
        ),
        Tool(
            "get_application",
            "Get one application: timeline, people (names and roles), reminders, offer, "
            "and job description.",
            GetApplication,
            read=get_application,
        ),
        Tool(
            "search_jobs",
            "Search the jobs Waypoint has already found (it can't fetch new ones). Best fit first.",
            SearchJobs,
            read=search_jobs,
        ),
        Tool("get_job", "Get one job posting, including its description.", GetJob, read=get_job),
        Tool(
            "get_vault_facts",
            "Get the user's verified career facts and STAR stories. "
            "Use these for any draft about their experience.",
            GetVaultFacts,
            read=get_vault_facts,
        ),
        Tool(
            "create_reminder",
            "Propose a reminder. The user must confirm it in the app.",
            CreateReminder,
            describe=describe_reminder,
            apply=apply_reminder,
        ),
        Tool(
            "move_application",
            "Propose moving an application to another stage. The user must confirm it.",
            MoveApplication,
            describe=describe_move,
            apply=apply_move,
        ),
        Tool(
            "add_note",
            "Propose adding a note to an application's timeline. The user must confirm it.",
            AddNote,
            describe=describe_note,
            apply=apply_note,
        ),
        Tool(
            "save_job",
            "Propose saving a job posting. The user must confirm it.",
            SaveJob,
            describe=describe_save,
            apply=apply_save,
        ),
        Tool(
            "track_job",
            "Propose adding a job posting to the tracker. The user must confirm it.",
            TrackJob,
            describe=describe_track,
            apply=apply_track,
        ),
    ]
}


def as_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
