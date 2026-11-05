# SPDX-License-Identifier: Apache-2.0
"""Per-user data access and output builders for the tracker.

Every query filters by ``user_id``. Another user's row is reported as "not found".
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from waypoint.db import Base
from waypoint.errors import ApiError
from waypoint.security import contexts
from waypoint.security.crypto import FieldCipher
from waypoint.tracker.models import (
    OPEN_STAGES,
    Application,
    ApplicationContact,
    ApplicationEvent,
    Attachment,
    Contact,
    Offer,
    Referral,
    Reminder,
)
from waypoint.tracker.schemas import (
    AgendaItem,
    ApplicationOut,
    ApplicationSummary,
    AttachmentOut,
    ContactLink,
    ContactOut,
    DueItem,
    EventOut,
    OfferOut,
    ReferralOut,
    ReminderOut,
    annual_cents,
    hourly_cents,
)

_CLOSED_REFERRAL = ("submitted", "declined")


def not_found(message: str = "Not found.") -> ApiError:
    return ApiError(404, "not_found", message)


async def get_owned[M: Base](
    db: AsyncSession, model: type[M], user_id: uuid.UUID, row_id: uuid.UUID, message: str = ""
) -> M:
    cols: Any = model  # every tracker model has user_id and id columns
    result = await db.execute(select(model).where(cols.user_id == user_id, cols.id == row_id))
    row: M | None = result.scalars().first()
    if row is None:
        raise not_found(message or "Not found.")
    return row


def move_stage(db: AsyncSession, app: Application, stage: str, note: str, today: date) -> bool:
    """Change an application's stage and record it in the timeline. Caller commits."""
    if stage == app.stage:
        return False
    now = datetime.now(UTC)
    db.add(
        ApplicationEvent(
            user_id=app.user_id,
            application_id=app.id,
            kind="stage_change",
            occurred_at=now,
            title=f"Moved to {stage.capitalize()}",
            body=note,
            data={"from": app.stage, "to": stage},
        )
    )
    app.stage = stage
    app.stage_changed_at = now
    app.updated_at = now
    if stage == "applied" and app.applied_on is None:
        app.applied_on = today
    return True


def contact_context(user_id: uuid.UUID, contact_id: uuid.UUID, field: str) -> str:
    return contexts.contact(user_id, contact_id, field)


def attachment_context(user_id: uuid.UUID, attachment_id: uuid.UUID) -> str:
    return contexts.attachment(user_id, attachment_id)


# --- output builders --------------------------------------------------------------------


def application_out(a: Application) -> ApplicationOut:
    return ApplicationOut.model_validate(a, from_attributes=True)


def attachment_out(a: Attachment) -> AttachmentOut:
    return AttachmentOut.model_validate(a, from_attributes=True)


def event_out(e: ApplicationEvent, files: Sequence[Attachment]) -> EventOut:
    return EventOut(
        id=e.id,
        application_id=e.application_id,
        kind=e.kind,
        occurred_at=e.occurred_at,
        title=e.title,
        body=e.body,
        data=e.data,
        attachments=[attachment_out(f) for f in files],
        created_at=e.created_at,
        updated_at=e.updated_at,
    )


def reminder_out(r: Reminder) -> ReminderOut:
    return ReminderOut.model_validate(r, from_attributes=True)


def referral_out(r: Referral, contact_name: str) -> ReferralOut:
    return ReferralOut(
        id=r.id,
        application_id=r.application_id,
        contact_id=r.contact_id,
        contact_name=contact_name,
        status=r.status,
        asked_on=r.asked_on,
        referred_on=r.referred_on,
        follow_up_on=r.follow_up_on,
        notes=r.notes,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


def offer_out(o: Offer, app: Application) -> OfferOut:
    annual = annual_cents(o.base_cents, o.base_period, o.hours_per_week)
    return OfferOut(
        id=o.id,
        application_id=o.application_id,
        application_title=app.title,
        company=app.company,
        stage=app.stage,
        currency=o.currency,
        base_cents=o.base_cents,
        base_period=o.base_period,
        hours_per_week=o.hours_per_week,
        bonus_cents=o.bonus_cents,
        bonus_notes=o.bonus_notes,
        equity_notes=o.equity_notes,
        pto_days=o.pto_days,
        schedule=o.schedule,
        commute_minutes=o.commute_minutes,
        growth_notes=o.growth_notes,
        benefits_notes=o.benefits_notes,
        deadline=o.deadline,
        annual_cents=annual,
        hourly_cents=hourly_cents(o.base_cents, o.base_period, o.hours_per_week),
        total_annual_cents=annual + (o.bonus_cents or 0),
        updated_at=o.updated_at,
    )


async def contact_links(
    db: AsyncSession, user_id: uuid.UUID, contact_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, list[ContactLink]]:
    ids = list(contact_ids)
    out: dict[uuid.UUID, list[ContactLink]] = {i: [] for i in ids}
    if not ids:
        return out
    rows = await db.execute(
        select(ApplicationContact, Application)
        .join(
            Application,
            (Application.id == ApplicationContact.application_id)
            & (Application.user_id == ApplicationContact.user_id),
        )
        .where(ApplicationContact.user_id == user_id, ApplicationContact.contact_id.in_(ids))
        .order_by(Application.updated_at.desc())
    )
    for link, app in rows.all():
        out[link.contact_id].append(
            ContactLink(
                application_id=app.id,
                role=link.role,
                title=app.title,
                company=app.company,
                stage=app.stage,
            )
        )
    return out


def contact_out(c: Contact, cipher: FieldCipher, links: list[ContactLink]) -> ContactOut:
    def dec(enc: str | None, field: str) -> str:
        return cipher.decrypt(enc, contact_context(c.user_id, c.id, field)) if enc else ""

    return ContactOut(
        id=c.id,
        name=c.name,
        company=c.company,
        title=c.title,
        email=dec(c.email_enc, "email"),
        phone=dec(c.phone_enc, "phone"),
        linkedin_url=c.linkedin_url,
        notes=c.notes,
        links=links,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


def set_contact_secrets(c: Contact, cipher: FieldCipher, email: str, phone: str) -> None:
    c.email_enc = (
        cipher.encrypt(email, contact_context(c.user_id, c.id, "email")) if email else None
    )
    c.phone_enc = (
        cipher.encrypt(phone, contact_context(c.user_id, c.id, "phone")) if phone else None
    )


# --- agenda ------------------------------------------------------------------------------


async def agenda_items(
    db: AsyncSession,
    user_id: uuid.UUID,
    until: date | None = None,
    application_id: uuid.UUID | None = None,
) -> list[AgendaItem]:
    """Open reminders plus deadlines and referral follow-ups, soonest first."""
    apps = {
        a.id: a
        for a in await db.scalars(
            select(Application).where(
                Application.user_id == user_id,
                *([Application.id == application_id] if application_id else []),
            )
        )
    }
    contact_rows = await db.execute(
        select(Contact.id, Contact.name).where(Contact.user_id == user_id)
    )
    contacts: dict[uuid.UUID, str] = dict(contact_rows.all())
    items: list[AgendaItem] = []

    def add(**kw: Any) -> None:
        app = apps.get(kw.get("application_id"))  # type: ignore[arg-type]
        kw.setdefault("due_time", None)
        kw.setdefault("contact_id", None)
        items.append(
            AgendaItem(
                application_title=app.title if app else "",
                company=app.company if app else "",
                contact_name=contacts.get(kw["contact_id"], "") if kw["contact_id"] else "",
                **kw,
            )
        )

    rq = select(Reminder).where(Reminder.user_id == user_id, Reminder.done_at.is_(None))
    if application_id:
        rq = rq.where(Reminder.application_id == application_id)
    if until:
        rq = rq.where(Reminder.due_on <= until)
    for r in await db.scalars(rq):
        add(
            source="reminder",
            id=r.id,
            title=r.title,
            kind=r.kind,
            due_on=r.due_on,
            due_time=r.due_time,
            application_id=r.application_id,
            contact_id=r.contact_id,
        )

    for a in apps.values():
        if (
            a.deadline
            and a.stage in OPEN_STAGES
            and a.stage != "offer"
            and (until is None or a.deadline <= until)
        ):
            add(
                source="application_deadline",
                id=a.id,
                title=f"Apply by: {a.title}",
                kind="deadline",
                due_on=a.deadline,
                application_id=a.id,
            )

    oq = select(Offer).where(Offer.user_id == user_id, Offer.deadline.is_not(None))
    for o in await db.scalars(oq):
        app = apps.get(o.application_id)
        if app is None or app.stage != "offer" or o.deadline is None:
            continue
        if until is None or o.deadline <= until:
            add(
                source="offer_deadline",
                id=o.id,
                title=f"Offer decision: {app.title}",
                kind="deadline",
                due_on=o.deadline,
                application_id=app.id,
            )

    fq = select(Referral).where(
        Referral.user_id == user_id,
        Referral.follow_up_on.is_not(None),
        Referral.status.not_in(_CLOSED_REFERRAL),
    )
    for ref in await db.scalars(fq):
        app = apps.get(ref.application_id)
        if app is None or ref.follow_up_on is None or app.stage not in OPEN_STAGES:
            continue
        if until is None or ref.follow_up_on <= until:
            add(
                source="referral_follow_up",
                id=ref.id,
                title=f"Follow up on referral: {contacts.get(ref.contact_id, '')}",
                kind="follow_up",
                due_on=ref.follow_up_on,
                application_id=app.id,
                contact_id=ref.contact_id,
            )

    items.sort(key=lambda i: (i.due_on, i.due_time is None, i.due_time or datetime.min.time()))
    return items


# --- board summaries ----------------------------------------------------------------------


async def summaries(
    db: AsyncSession, user_id: uuid.UUID, apps: Sequence[Application]
) -> list[ApplicationSummary]:
    ids = [a.id for a in apps]
    if not ids:
        return []
    contacts: dict[uuid.UUID, int] = dict(
        (
            await db.execute(
                select(ApplicationContact.application_id, func.count())
                .where(
                    ApplicationContact.user_id == user_id,
                    ApplicationContact.application_id.in_(ids),
                )
                .group_by(ApplicationContact.application_id)
            )
        ).all()
    )
    offers = set(
        await db.scalars(
            select(Offer.application_id).where(
                Offer.user_id == user_id, Offer.application_id.in_(ids)
            )
        )
    )
    last_event: dict[uuid.UUID, datetime] = dict(
        (
            await db.execute(
                select(ApplicationEvent.application_id, func.max(ApplicationEvent.occurred_at))
                .where(
                    ApplicationEvent.user_id == user_id,
                    ApplicationEvent.application_id.in_(ids),
                )
                .group_by(ApplicationEvent.application_id)
            )
        ).all()
    )
    next_due: dict[uuid.UUID, DueItem] = {}
    for item in await agenda_items(db, user_id):
        if item.application_id and item.application_id not in next_due:
            next_due[item.application_id] = DueItem(
                due_on=item.due_on, due_time=item.due_time, title=item.title, source=item.source
            )
    out = []
    for a in apps:
        activity = max(a.updated_at, last_event.get(a.id, a.updated_at))
        out.append(
            ApplicationSummary(
                id=a.id,
                title=a.title,
                company=a.company,
                location=a.location,
                work_mode=a.work_mode,
                pay_min_cents=a.pay_min_cents,
                pay_max_cents=a.pay_max_cents,
                pay_period=a.pay_period,
                currency=a.currency,
                stage=a.stage,
                stage_changed_at=a.stage_changed_at,
                applied_on=a.applied_on,
                deadline=a.deadline,
                priority=a.priority,
                updated_at=a.updated_at,
                last_activity_at=activity,
                next_due=next_due.get(a.id),
                contact_count=contacts.get(a.id, 0),
                has_offer=a.id in offers,
            )
        )
    return out


async def files_by_event(
    db: AsyncSession, user_id: uuid.UUID, application_id: uuid.UUID
) -> tuple[dict[uuid.UUID, list[Attachment]], list[Attachment]]:
    """Attachments for an application, split into per-event lists and loose files.

    Only metadata columns are loaded, never the encrypted contents.
    """
    rows = await db.scalars(
        select(Attachment)
        .options(defer(Attachment.content_enc, raiseload=True))
        .where(Attachment.user_id == user_id, Attachment.application_id == application_id)
        .order_by(Attachment.created_at)
    )
    by_event: dict[uuid.UUID, list[Attachment]] = defaultdict(list)
    loose: list[Attachment] = []
    for f in rows:
        if f.event_id:
            by_event[f.event_id].append(f)
        else:
            loose.append(f)
    return by_event, loose
