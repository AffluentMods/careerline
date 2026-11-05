# SPDX-License-Identifier: Apache-2.0
"""Tracker API: contacts, application links, referrals, reminders, and the agenda."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from careerline import audit
from careerline.auth.sessions import DB, CipherDep, CurrentUser, csrf_protect, utcnow
from careerline.errors import ApiError
from careerline.tracker import service
from careerline.tracker.models import (
    Application,
    ApplicationContact,
    Contact,
    Referral,
    Reminder,
)
from careerline.tracker.schemas import (
    AgendaItem,
    ContactIn,
    ContactOut,
    ContactPatch,
    LinkedContactOut,
    LinkIn,
    ReferralIn,
    ReferralOut,
    ReferralPatch,
    ReminderIn,
    ReminderOut,
    ReminderPatch,
)

router = APIRouter(prefix="/api/v1/tracker", tags=["tracker"], dependencies=[Depends(csrf_protect)])

_MAX_AGENDA_DAYS = 366


# --- contacts ----------------------------------------------------------------------------


@router.get("/contacts")
async def list_contacts(
    auth: CurrentUser,
    db: DB,
    cipher: CipherDep,
    q: str | None = Query(default=None, max_length=200),
) -> list[ContactOut]:
    uid = auth.user.id
    contacts = list(
        await db.scalars(select(Contact).where(Contact.user_id == uid).order_by(Contact.name))
    )
    if q and q.strip():
        needle = q.strip().casefold()
        contacts = [
            c for c in contacts if any(needle in v.casefold() for v in (c.name, c.company, c.title))
        ]
    links = await service.contact_links(db, uid, (c.id for c in contacts))
    return [service.contact_out(c, cipher, links[c.id]) for c in contacts]


@router.post("/contacts", status_code=201)
async def create_contact(
    body: ContactIn, auth: CurrentUser, db: DB, cipher: CipherDep
) -> ContactOut:
    uid = auth.user.id
    contact = Contact(
        id=uuid.uuid4(),
        user_id=uid,
        **body.model_dump(exclude={"email", "phone"}),
    )
    service.set_contact_secrets(contact, cipher, body.email, body.phone)
    db.add(contact)
    await db.commit()
    await db.refresh(contact)
    return service.contact_out(contact, cipher, [])


@router.get("/contacts/{contact_id}")
async def get_contact(
    contact_id: uuid.UUID, auth: CurrentUser, db: DB, cipher: CipherDep
) -> ContactOut:
    contact = await service.get_owned(db, Contact, auth.user.id, contact_id)
    links = await service.contact_links(db, auth.user.id, [contact.id])
    return service.contact_out(contact, cipher, links[contact.id])


@router.patch("/contacts/{contact_id}")
async def patch_contact(
    contact_id: uuid.UUID, body: ContactPatch, auth: CurrentUser, db: DB, cipher: CipherDep
) -> ContactOut:
    uid = auth.user.id
    contact = await service.get_owned(db, Contact, uid, contact_id)
    current = service.contact_out(contact, cipher, [])
    changes = body.model_dump(exclude_none=True)
    for key in ("name", "company", "title", "linkedin_url", "notes"):
        if key in changes:
            setattr(contact, key, changes[key])
    service.set_contact_secrets(
        contact, cipher, changes.get("email", current.email), changes.get("phone", current.phone)
    )
    await db.commit()
    await db.refresh(contact)
    links = await service.contact_links(db, uid, [contact.id])
    return service.contact_out(contact, cipher, links[contact.id])


@router.delete("/contacts/{contact_id}", status_code=204)
async def delete_contact(
    contact_id: uuid.UUID, request: Request, auth: CurrentUser, db: DB
) -> Response:
    uid = auth.user.id
    contact = await service.get_owned(db, Contact, uid, contact_id)
    # Reminders about this person stay, without the link. Links and referrals cascade.
    await db.execute(
        update(Reminder)
        .where(Reminder.user_id == uid, Reminder.contact_id == contact.id)
        .values(contact_id=None)
    )
    await db.delete(contact)
    audit.record(db, request, "tracker.contact_deleted", uid, contact_id=str(contact.id))
    await db.commit()
    return Response(status_code=204)


# --- links ----------------------------------------------------------------------------------


@router.put("/applications/{application_id}/contacts/{contact_id}")
async def link_contact(
    application_id: uuid.UUID,
    contact_id: uuid.UUID,
    body: LinkIn,
    auth: CurrentUser,
    db: DB,
    cipher: CipherDep,
) -> LinkedContactOut:
    uid = auth.user.id
    app = await service.get_owned(db, Application, uid, application_id)
    contact = await service.get_owned(db, Contact, uid, contact_id)
    link = await db.scalar(
        select(ApplicationContact).where(
            ApplicationContact.user_id == uid,
            ApplicationContact.application_id == app.id,
            ApplicationContact.contact_id == contact.id,
        )
    )
    if link is None:
        db.add(
            ApplicationContact(
                user_id=uid, application_id=app.id, contact_id=contact.id, role=body.role
            )
        )
    else:
        link.role = body.role
    app.updated_at = utcnow()
    await db.commit()
    links = await service.contact_links(db, uid, [contact.id])
    return LinkedContactOut(
        contact=service.contact_out(contact, cipher, links[contact.id]), role=body.role
    )


@router.delete("/applications/{application_id}/contacts/{contact_id}", status_code=204)
async def unlink_contact(
    application_id: uuid.UUID, contact_id: uuid.UUID, auth: CurrentUser, db: DB
) -> Response:
    result = await db.execute(
        delete(ApplicationContact).where(
            ApplicationContact.user_id == auth.user.id,
            ApplicationContact.application_id == application_id,
            ApplicationContact.contact_id == contact_id,
        )
    )
    if result.rowcount == 0:  # type: ignore[attr-defined]
        raise service.not_found()
    await db.commit()
    return Response(status_code=204)


# --- referrals ------------------------------------------------------------------------------


@router.post("/applications/{application_id}/referrals", status_code=201)
async def create_referral(
    application_id: uuid.UUID, body: ReferralIn, auth: CurrentUser, db: DB
) -> ReferralOut:
    uid = auth.user.id
    app = await service.get_owned(db, Application, uid, application_id)
    contact = await service.get_owned(db, Contact, uid, body.contact_id, "Contact not found.")
    exists = await db.scalar(
        select(Referral.id).where(
            Referral.user_id == uid,
            Referral.application_id == app.id,
            Referral.contact_id == contact.id,
        )
    )
    if exists:
        raise ApiError(
            409, "referral_exists", "This contact already has a referral for this application."
        )
    referral = Referral(user_id=uid, application_id=app.id, **body.model_dump())
    db.add(referral)
    # A referrer is always linked to the application too.
    link = await db.scalar(
        select(ApplicationContact).where(
            ApplicationContact.user_id == uid,
            ApplicationContact.application_id == app.id,
            ApplicationContact.contact_id == contact.id,
        )
    )
    if link is None:
        db.add(
            ApplicationContact(
                user_id=uid, application_id=app.id, contact_id=contact.id, role="referrer"
            )
        )
    await db.commit()
    await db.refresh(referral)
    return service.referral_out(referral, contact.name)


@router.patch("/referrals/{referral_id}")
async def patch_referral(
    referral_id: uuid.UUID, body: ReferralPatch, auth: CurrentUser, db: DB
) -> ReferralOut:
    uid = auth.user.id
    referral = await service.get_owned(db, Referral, uid, referral_id)
    for key in body.model_fields_set:
        value = getattr(body, key)
        if value is None and key in {"status", "notes"}:
            continue
        setattr(referral, key, value)
    await db.commit()
    await db.refresh(referral)
    name = await db.scalar(select(Contact.name).where(Contact.id == referral.contact_id))
    return service.referral_out(referral, name or "")


@router.delete("/referrals/{referral_id}", status_code=204)
async def delete_referral(referral_id: uuid.UUID, auth: CurrentUser, db: DB) -> Response:
    referral = await service.get_owned(db, Referral, auth.user.id, referral_id)
    await db.delete(referral)
    await db.commit()
    return Response(status_code=204)


# --- reminders & agenda ----------------------------------------------------------------------


async def _check_reminder_links(
    db: AsyncSession, uid: uuid.UUID, application_id: uuid.UUID | None, contact_id: uuid.UUID | None
) -> None:
    if application_id:
        await service.get_owned(db, Application, uid, application_id, "Application not found.")
    if contact_id:
        await service.get_owned(db, Contact, uid, contact_id, "Contact not found.")


@router.get("/reminders")
async def list_reminders(
    auth: CurrentUser,
    db: DB,
    include_done: bool = False,
    application_id: uuid.UUID | None = None,
) -> list[ReminderOut]:
    stmt = select(Reminder).where(Reminder.user_id == auth.user.id)
    if not include_done:
        stmt = stmt.where(Reminder.done_at.is_(None))
    if application_id:
        stmt = stmt.where(Reminder.application_id == application_id)
    rows = await db.scalars(stmt.order_by(Reminder.due_on, Reminder.due_time.nulls_last()))
    return [service.reminder_out(r) for r in rows]


@router.post("/reminders", status_code=201)
async def create_reminder(body: ReminderIn, auth: CurrentUser, db: DB) -> ReminderOut:
    uid = auth.user.id
    await _check_reminder_links(db, uid, body.application_id, body.contact_id)
    reminder = Reminder(user_id=uid, **body.model_dump())
    db.add(reminder)
    await db.commit()
    await db.refresh(reminder)
    return service.reminder_out(reminder)


@router.patch("/reminders/{reminder_id}")
async def patch_reminder(
    reminder_id: uuid.UUID, body: ReminderPatch, auth: CurrentUser, db: DB
) -> ReminderOut:
    uid = auth.user.id
    reminder = await service.get_owned(db, Reminder, uid, reminder_id)
    fields = body.model_fields_set
    await _check_reminder_links(
        db,
        uid,
        body.application_id if "application_id" in fields else None,
        body.contact_id if "contact_id" in fields else None,
    )
    for key in fields - {"done"}:
        value = getattr(body, key)
        if value is None and key in {"title", "kind", "due_on", "notes"}:
            continue
        setattr(reminder, key, value)
    if body.done is not None:
        reminder.done_at = utcnow() if body.done else None
    await db.commit()
    await db.refresh(reminder)
    return service.reminder_out(reminder)


@router.delete("/reminders/{reminder_id}", status_code=204)
async def delete_reminder(reminder_id: uuid.UUID, auth: CurrentUser, db: DB) -> Response:
    reminder = await service.get_owned(db, Reminder, auth.user.id, reminder_id)
    await db.delete(reminder)
    await db.commit()
    return Response(status_code=204)


@router.get("/agenda")
async def agenda(
    auth: CurrentUser,
    db: DB,
    today: date | None = None,
    days: int = Query(default=14, ge=0, le=_MAX_AGENDA_DAYS),
) -> list[AgendaItem]:
    """Everything overdue plus what's due in the next ``days`` days (browser-local dates)."""
    day = today or datetime.now(UTC).date()
    return await service.agenda_items(db, auth.user.id, until=day + timedelta(days=days))
