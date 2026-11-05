# SPDX-License-Identifier: Apache-2.0
"""Demo mode: a fictional job seeker with a filled-in vault, tracker, and job matches.

Everything here is made up: the person, the companies, the contacts (example.com
addresses and 555-01xx phone numbers). Demo mode (``CAREERLINE_DEMO_MODE=true``) seeds this
on first start, closes registration, and stops anything from leaving the server.

    python -m careerline.demo     # seed into the configured database (if the demo user is missing)
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from careerline.ai.scoring import score_posting
from careerline.discovery.models import JobFilters, JobPosting, JobSource
from careerline.discovery.normalize import fingerprint
from careerline.models import Story, StoryEntry, User, VaultEntry, VaultProfile
from careerline.security import contexts, passwords
from careerline.security.crypto import FieldCipher
from careerline.tracker.models import (
    Application,
    ApplicationContact,
    ApplicationEvent,
    Contact,
    Offer,
    Reminder,
)
from careerline.tracker.service import set_contact_secrets
from careerline.vault.schemas import parse_data, sort_date

DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "explore-careerline"  # noqa: S105 - fictional demo account, shown on the login page


def _at(day: date, hour: int = 10) -> datetime:
    return datetime.combine(day, time(hour), tzinfo=UTC)


async def seed_demo(
    db: AsyncSession, cipher: FieldCipher, today: date | None = None
) -> User | None:
    """Create the demo account and its data. Returns None if it already exists."""
    if await db.scalar(select(exists().where(User.email == DEMO_EMAIL))):
        return None
    today = today or datetime.now(UTC).date()
    has_users = bool(await db.scalar(select(exists().select_from(User))))
    user = User(
        email=DEMO_EMAIL,
        display_name="Riley Morgan",
        password_hash=await passwords.hash_password(DEMO_PASSWORD),
        is_owner=not has_users,
    )
    db.add(user)
    await db.flush()
    uid = user.id
    await _vault(db, cipher, uid)
    await _tracker(db, cipher, uid, today)
    await _jobs(db, uid, today)
    return user


# --- vault -----------------------------------------------------------------------------------


async def _vault(db: AsyncSession, cipher: FieldCipher, uid: uuid.UUID) -> None:
    profile = VaultProfile(
        user_id=uid,
        full_name="Riley Morgan",
        headline="Data analyst who turns messy operations data into decisions",
        location_text="Riverton, WA",
        summary=(
            "Five years of reporting and analytics in logistics and financial services. "
            "Strongest in SQL, dashboards, and explaining numbers to people who need to act "
            "on them."
        ),
    )
    profile.email_enc = cipher.encrypt("riley.morgan@example.com", contexts.profile(uid, "email"))
    profile.phone_enc = cipher.encrypt("(555) 010-0142", contexts.profile(uid, "phone"))
    db.add(profile)

    def entry(
        kind: str, data: dict[str, Any], parent: VaultEntry | None = None, verified: bool = True
    ) -> VaultEntry:
        clean = parse_data(kind, data)
        e = VaultEntry(
            id=uuid.uuid4(),
            user_id=uid,
            kind=kind,
            parent_id=parent.id if parent else None,
            data=clean,
            verified=verified,
            verified_at=datetime.now(UTC) if verified else None,
            sort_date=sort_date(kind, clean),
        )
        db.add(e)
        return e

    lumen = entry(
        "role",
        {
            "title": "Operations Data Analyst",
            "employer": "Lumen Freight",
            "location": "Riverton, WA",
            "employment_type": "full_time",
            "start": "2022-03",
            "summary": "Analytics for a regional freight carrier's dispatch and customer teams.",
        },
    )
    await db.flush()
    entry(
        "responsibility",
        {"text": "Build weekly dispatch dashboards in Power BI for 40 dispatchers and managers"},
        lumen,
    )
    entry(
        "responsibility",
        {"text": "Maintain SQL models for shipment, route, and delivery data"},
        lumen,
    )
    late = entry(
        "achievement",
        {
            "text": "Cut late deliveries by flagging at-risk routes in a morning report",
            "metric": {
                "value": "18",
                "unit": "%",
                "context": "fewer late deliveries in six months",
            },
        },
        lumen,
    )
    entry(
        "achievement",
        {"text": "Replaced 14 spreadsheet reports with one self-serve dashboard"},
        lumen,
    )
    orchard = entry(
        "role",
        {
            "title": "Reporting Specialist",
            "employer": "Orchard Credit Union",
            "location": "Riverton, WA",
            "employment_type": "full_time",
            "start": "2019-06",
            "end": "2022-02",
        },
    )
    await db.flush()
    entry(
        "responsibility",
        {"text": "Produced month-end member and loan reports for leadership"},
        orchard,
    )
    month_end = entry(
        "achievement",
        {
            "text": "Automated month-end reporting with Python and SQL",
            "metric": {"value": "12", "unit": "hours", "context": "saved each month"},
        },
        orchard,
    )
    for name, level in [
        ("SQL", "expert"),
        ("Excel", "expert"),
        ("Python", "proficient"),
        ("Power BI", "proficient"),
        ("Statistics", "proficient"),
        ("dbt", "familiar"),
        ("Tableau", "familiar"),
    ]:
        entry("skill", {"name": name, "category": "Data", "level": level})
    entry(
        "skill", {"name": "Apache Spark", "category": "Data", "level": "familiar"}, verified=False
    )
    entry(
        "certification",
        {
            "name": "Certified Data Practitioner",
            "issuer": "Data Skills Council",
            "earned_on": "2021-09",
        },
    )
    entry(
        "education",
        {
            "institution": "Riverton Community College",
            "credential": "A.A.S.",
            "field": "Business Information Systems",
            "start": "2016-09",
            "end": "2018-06",
        },
    )
    entry(
        "project",
        {
            "name": "Bike-share demand forecast",
            "your_role": "Solo project",
            "start": "2023-01",
            "end": "2023-04",
            "description": "Forecast station demand from weather and calendar data in Python.",
            "url": "https://example.com/riley/bike-share",
        },
    )
    entry("link", {"label": "Portfolio", "url": "https://example.com/riley"})
    await db.flush()

    stories = [
        (
            "Fixing late deliveries with a morning risk report",
            "Late deliveries were rising and dispatch found out only after customers complained.",
            "Give dispatchers a way to see risky routes before trucks left.",
            "Combined route, weather, and driver-hours data in SQL and built a 7 a.m. report "
            "that ranked routes by risk; walked each shift lead through it.",
            "Late deliveries fell 18% over six months and the report became part of the "
            "daily huddle.",
            ["impact", "data", "stakeholders"],
            late,
        ),
        (
            "Automating month-end reporting",
            "Month-end reports took two analysts most of a week, copying numbers between "
            "spreadsheets.",
            "Make the reports faster and less error-prone without new tools.",
            "Wrote Python and SQL jobs that pulled the numbers and filled the templates, "
            "then checked them side by side with the manual version for two months.",
            "Saved about 12 hours a month and ended the copy-paste errors.",
            ["automation", "process"],
            month_end,
        ),
    ]
    for title, s, t, a, r, tags, linked in stories:
        story = Story(
            id=uuid.uuid4(),
            user_id=uid,
            title=title,
            situation=s,
            task=t,
            action=a,
            result=r,
            tags=tags,
            verified=True,
            verified_at=datetime.now(UTC),
        )
        db.add(story)
        await db.flush()
        db.add(StoryEntry(story_id=story.id, entry_id=linked.id, user_id=uid))


# --- tracker ---------------------------------------------------------------------------------


async def _tracker(db: AsyncSession, cipher: FieldCipher, uid: uuid.UUID, today: date) -> None:
    def d(days: int) -> date:
        return today + timedelta(days=days)

    # (title, company, source, work mode, pay, [(stage, days from today)], deadline)
    plans: list[
        tuple[str, str, str, str, tuple[int, int, str] | None, list[tuple[str, int]], int | None]
    ] = [
        (
            "Senior Data Analyst",
            "Kestrel Software",
            "Referral",
            "hybrid",
            (9_000_000, 105_000_00, "year"),
            [("applied", -40), ("screening", -33), ("interviewing", -25), ("offer", -6)],
            None,
        ),
        (
            "Business Intelligence Analyst",
            "Brightwater Health",
            "Company site",
            "hybrid",
            (4_200, 5_000, "hour"),
            [("applied", -30), ("screening", -21), ("interviewing", -14)],
            None,
        ),
        (
            "Data Quality Analyst",
            "Granite Peak Insurance",
            "Referral",
            "remote",
            (8_000_000, 9_500_000, "year"),
            [("applied", -18), ("interviewing", -8)],
            None,
        ),
        (
            "Analytics Engineer",
            "Ironleaf Labs",
            "Recruiter",
            "remote",
            (10_500_000, 12_500_000, "year"),
            [("applied", -20), ("screening", -15)],
            None,
        ),
        (
            "Data Analyst II",
            "Harborlight Analytics",
            "Job board",
            "onsite",
            None,
            [("applied", -28), ("rejected", -12)],
            None,
        ),
        (
            "Operations Analyst",
            "Summit Ridge Energy",
            "Job board",
            "onsite",
            (3_800, 4_400, "hour"),
            [("applied", -60), ("rejected", -50)],
            None,
        ),
        (
            "Junior Data Scientist",
            "Tidewater University",
            "Job board",
            "hybrid",
            None,
            [("applied", -35), ("withdrawn", -20)],
            None,
        ),
        (
            "Reporting Analyst",
            "Maple Transit Authority",
            "Company site",
            "onsite",
            (3_600, 4_100, "hour"),
            [("applied", -45)],
            None,
        ),
        (
            "Data Analyst",
            "Bluefin Media",
            "Job board",
            "remote",
            (7_500_000, 8_500_000, "year"),
            [("applied", -10)],
            None,
        ),
        (
            "Marketing Data Analyst",
            "Cedar & Pine Outfitters",
            "Company site",
            "hybrid",
            (4_000, 4_800, "hour"),
            [],
            7,
        ),
    ]
    apps: dict[str, Application] = {}
    for title, company, source, mode, pay, steps, deadline in plans:
        created = d(steps[0][1] - 2) if steps else d(-3)
        app = Application(
            id=uuid.uuid4(),
            user_id=uid,
            title=title,
            company=company,
            location="Riverton, WA" if mode != "remote" else "Remote",
            work_mode=mode,
            source=source,
            pay_min_cents=pay[0] if pay else None,
            pay_max_cents=pay[1] if pay else None,
            pay_period=pay[2] if pay else None,
            description=f"{title} at {company}. SQL, dashboards, and working with business teams.",
            stage="saved",
            stage_changed_at=_at(created),
            deadline=d(deadline) if deadline is not None else None,
            created_at=_at(created),
            updated_at=_at(created),
        )
        db.add(app)
        await db.flush()
        previous = "saved"
        for stage, offset in steps:
            when = _at(d(offset), 9 + len(stage) % 6)
            db.add(
                ApplicationEvent(
                    user_id=uid,
                    application_id=app.id,
                    kind="stage_change",
                    occurred_at=when,
                    title=f"Moved to {stage.capitalize()}",
                    data={"from": previous, "to": stage},
                    created_at=when,
                    updated_at=when,
                )
            )
            if stage == "applied":
                app.applied_on = d(offset)
            app.stage, app.stage_changed_at, previous = stage, when, stage
        apps[company] = app

    brightwater = apps["Brightwater Health"]
    db.add(
        ApplicationEvent(
            user_id=uid,
            application_id=brightwater.id,
            kind="interview",
            occurred_at=_at(d(-14), 14),
            title="Video interview with the analytics manager",
            body="Asked about dashboard adoption and working with nurses' schedules.",
            data={"interview_format": "video"},
        )
    )
    recruiter = Contact(
        id=uuid.uuid4(),
        user_id=uid,
        name="Jordan Avery",
        company="Ironleaf Labs",
        title="Technical Recruiter",
        notes="Prefers email. Said the team ships every two weeks.",
    )
    set_contact_secrets(recruiter, cipher, "jordan.avery@example.com", "(555) 010-0178")
    friend = Contact(
        id=uuid.uuid4(),
        user_id=uid,
        name="Sam Patel",
        company="Kestrel Software",
        title="Data Engineering Lead",
        notes="Former teammate at Lumen Freight; referred me.",
    )
    set_contact_secrets(friend, cipher, "sam.patel@example.com", "")
    db.add_all([recruiter, friend])
    await db.flush()
    db.add_all(
        [
            ApplicationContact(
                application_id=apps["Ironleaf Labs"].id,
                contact_id=recruiter.id,
                user_id=uid,
                role="recruiter",
            ),
            ApplicationContact(
                application_id=apps["Kestrel Software"].id,
                contact_id=friend.id,
                user_id=uid,
                role="referrer",
            ),
            Reminder(
                user_id=uid,
                title="Follow up on the Reporting Analyst application",
                kind="follow_up",
                due_on=d(-1),
                application_id=apps["Maple Transit Authority"].id,
            ),
            Reminder(
                user_id=uid,
                title="Send a thank-you note to Granite Peak",
                kind="task",
                due_on=d(0),
                application_id=apps["Granite Peak Insurance"].id,
            ),
            Reminder(
                user_id=uid,
                title="Prepare for the Brightwater panel interview",
                kind="interview",
                due_on=d(2),
                due_time=time(13, 30),
                application_id=brightwater.id,
            ),
            Reminder(
                user_id=uid,
                title="Check in with Jordan about next steps",
                kind="follow_up",
                due_on=d(4),
                application_id=apps["Ironleaf Labs"].id,
                contact_id=recruiter.id,
            ),
            Offer(
                user_id=uid,
                application_id=apps["Kestrel Software"].id,
                currency="USD",
                base_cents=98_000_00,
                base_period="year",
                hours_per_week=40,
                bonus_notes="Up to 8% annual bonus",
                pto_days=20,
                schedule="Hybrid, 2 days in office",
                commute_minutes=25,
                deadline=d(5),
            ),
        ]
    )


# --- discovery -------------------------------------------------------------------------------


async def _jobs(db: AsyncSession, uid: uuid.UUID, today: date) -> None:
    db.add(
        JobFilters(
            user_id=uid,
            min_hourly_cents=3_500,
            target_hourly_cents=4_800,
            exclude_keywords=["unpaid"],
            preferred_companies=["Kestrel Software"],
        )
    )
    db.add(
        JobSource(
            user_id=uid,
            kind="greenhouse",
            name="Demo feed (off in demo mode)",
            enabled=False,
            config={"board": "harborlight-demo", "company": "Harborlight Analytics"},
        )
    )
    postings = [
        (
            "Senior Reporting Analyst",
            "Orchard Valley Foods",
            "Riverton, WA",
            "hybrid",
            (4_600, 5_400, "hour"),
            "Own weekly sales and inventory reporting. Strong SQL and Power BI required; "
            "Python a plus. "
            "Work with store operations managers to turn questions into dashboards.",
        ),
        (
            "Data Analyst, Logistics",
            "Northgate Shipping Co.",
            "Remote",
            "remote",
            (8_800_000, 10_200_000, "year"),
            "Analyze shipment and route data to improve on-time delivery. SQL, Excel, and "
            "dashboarding "
            "(Power BI or Tableau). Experience in freight or logistics preferred.",
        ),
        (
            "Analytics Engineer",
            "Quillfeather Books",
            "Remote",
            "remote",
            (11_000_000, 13_000_000, "year"),
            "Build and test dbt models on a modern warehouse. Python, SQL, version control, and "
            "data quality testing. Two or more years in analytics engineering.",
        ),
        (
            "Business Analyst",
            "Riverton School District",
            "Riverton, WA",
            "onsite",
            (3_900, 4_500, "hour"),
            "Gather requirements, document processes, and produce reports for district leadership. "
            "Excel and SQL helpful.",
        ),
        (
            "Machine Learning Engineer",
            "Vantage Robotics",
            "Riverton, WA",
            "onsite",
            (14_000_000, 17_000_000, "year"),
            "Deploy deep learning models to robots. C++, CUDA, PyTorch, and five years of "
            "ML engineering.",
        ),
        (
            "Data Entry Clerk",
            "QuickCash Rewards",
            "Remote",
            "remote",
            None,
            "Earn fast money from home! No experience needed. Pay a small starter-kit fee "
            "to begin.",
        ),
    ]
    now = datetime.now(UTC)
    for i, (title, company, location, mode, pay, description) in enumerate(postings):
        p = JobPosting(
            id=uuid.uuid4(),
            user_id=uid,
            fingerprint=fingerprint(company, title, location),
            title=title,
            company=company,
            location=location,
            work_mode=mode,
            pay_min_cents=pay[0] if pay else None,
            pay_max_cents=pay[1] if pay else None,
            pay_period=pay[2] if pay else None,
            description=description,
            url=f"https://example.com/jobs/{hashlib.sha256(title.encode()).hexdigest()[:10]}",
            posted_at=now - timedelta(days=i + 1),
            first_seen_at=now - timedelta(hours=6 * i + 1),
            last_seen_at=now,
            status="new" if i != 3 else "saved",
        )
        db.add(p)
        await db.flush()
        await score_posting(db, uid, p, ai=None)


async def _main() -> int:
    from careerline import db as dbmod
    from careerline.config import get_settings

    settings = get_settings()
    dbmod.init_engine(settings.async_database_url)
    try:
        async with dbmod.get_sessionmaker()() as session:
            user = await seed_demo(
                session, FieldCipher(settings.encryption_keys.get_secret_value())
            )
            await session.commit()
    finally:
        await dbmod.dispose_engine()
    if user is None:
        print(f"The demo account ({DEMO_EMAIL}) already exists.")
    else:
        print(f"Demo data created. Sign in as {DEMO_EMAIL} / {DEMO_PASSWORD}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
