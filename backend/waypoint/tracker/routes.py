# SPDX-License-Identifier: Apache-2.0
"""Tracker API: applications, stage changes, timeline, offers, and the summary."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import delete, func, select, update

from waypoint import audit
from waypoint.auth.sessions import DB, CipherDep, CurrentUser, csrf_protect, utcnow
from waypoint.discovery.models import JobPosting
from waypoint.errors import ApiError
from waypoint.tracker import service
from waypoint.tracker.models import (
    OPEN_STAGES,
    STAGES,
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
    NULLABLE_APPLICATION_FIELDS,
    ApplicationDetailOut,
    ApplicationIn,
    ApplicationOut,
    ApplicationPatch,
    ApplicationSummary,
    EventIn,
    EventOut,
    EventPatch,
    LinkedContactOut,
    OfferIn,
    OfferOut,
    Stage,
    StageIn,
    TrackerSummary,
    _check_pay,
)

router = APIRouter(prefix="/api/v1/tracker", tags=["tracker"], dependencies=[Depends(csrf_protect)])


def _today(value: date | None) -> date:
    return value or datetime.now(UTC).date()


# --- applications --------------------------------------------------------------------------


@router.get("/applications")
async def list_applications(
    auth: CurrentUser,
    db: DB,
    stage: Stage | None = None,
    q: str | None = Query(default=None, max_length=200),
) -> list[ApplicationSummary]:
    uid = auth.user.id
    stmt = select(Application).where(Application.user_id == uid)
    if stage:
        stmt = stmt.where(Application.stage == stage)
    apps = list(await db.scalars(stmt.order_by(Application.updated_at.desc())))
    if q and q.strip():
        needle = q.strip().casefold()
        apps = [
            a
            for a in apps
            if any(needle in v.casefold() for v in (a.title, a.company, a.location, a.source))
        ]
    return await service.summaries(db, uid, apps)


@router.post("/applications", status_code=201)
async def create_application(body: ApplicationIn, auth: CurrentUser, db: DB) -> ApplicationOut:
    uid = auth.user.id
    app = Application(user_id=uid, **body.model_dump())
    db.add(app)
    await db.flush()
    db.add(
        ApplicationEvent(
            user_id=uid,
            application_id=app.id,
            kind="stage_change",
            occurred_at=utcnow(),
            title=f"Added as {body.stage.capitalize()}",
            data={"from": None, "to": body.stage},
        )
    )
    await db.commit()
    await db.refresh(app)
    return service.application_out(app)


@router.get("/applications/{application_id}")
async def get_application(
    application_id: uuid.UUID, auth: CurrentUser, db: DB, cipher: CipherDep
) -> ApplicationDetailOut:
    uid = auth.user.id
    app = await service.get_owned(db, Application, uid, application_id)
    by_event, loose = await service.files_by_event(db, uid, app.id)
    events = list(
        await db.scalars(
            select(ApplicationEvent)
            .where(ApplicationEvent.user_id == uid, ApplicationEvent.application_id == app.id)
            .order_by(ApplicationEvent.occurred_at.desc(), ApplicationEvent.created_at.desc())
        )
    )
    linked = (
        await db.execute(
            select(ApplicationContact, Contact)
            .join(
                Contact,
                (Contact.id == ApplicationContact.contact_id)
                & (Contact.user_id == ApplicationContact.user_id),
            )
            .where(ApplicationContact.user_id == uid, ApplicationContact.application_id == app.id)
            .order_by(Contact.name)
        )
    ).all()
    linked_rows = list(linked)
    links = await service.contact_links(db, uid, (c.id for _, c in linked_rows))
    referrals = (
        await db.execute(
            select(Referral, Contact.name)
            .join(
                Contact,
                (Contact.id == Referral.contact_id) & (Contact.user_id == Referral.user_id),
            )
            .where(Referral.user_id == uid, Referral.application_id == app.id)
            .order_by(Referral.created_at)
        )
    ).all()
    reminders = await db.scalars(
        select(Reminder)
        .where(Reminder.user_id == uid, Reminder.application_id == app.id)
        .order_by(Reminder.done_at.is_not(None), Reminder.due_on)
    )
    offer = await db.scalar(
        select(Offer).where(Offer.user_id == uid, Offer.application_id == app.id)
    )
    return ApplicationDetailOut(
        application=service.application_out(app),
        events=[service.event_out(e, by_event.get(e.id, [])) for e in events],
        attachments=[service.attachment_out(f) for f in loose],
        contacts=[
            LinkedContactOut(contact=service.contact_out(c, cipher, links[c.id]), role=link.role)
            for link, c in linked_rows
        ],
        referrals=[service.referral_out(r, name) for r, name in referrals],
        reminders=[service.reminder_out(r) for r in reminders],
        offer=service.offer_out(offer, app) if offer else None,
    )


@router.patch("/applications/{application_id}")
async def patch_application(
    application_id: uuid.UUID, body: ApplicationPatch, auth: CurrentUser, db: DB
) -> ApplicationOut:
    app = await service.get_owned(db, Application, auth.user.id, application_id)
    for key in body.model_fields_set:
        value = getattr(body, key)
        if value is None and key not in NULLABLE_APPLICATION_FIELDS:
            continue  # null for a required field means "unchanged"
        setattr(app, key, value)
    try:
        _check_pay(app.pay_min_cents, app.pay_max_cents, app.pay_period)
    except ValueError as exc:
        raise ApiError(422, "invalid_pay", str(exc)) from None
    await db.commit()
    await db.refresh(app)
    return service.application_out(app)


@router.post("/applications/{application_id}/stage")
async def change_stage(
    application_id: uuid.UUID, body: StageIn, auth: CurrentUser, db: DB
) -> ApplicationOut:
    uid = auth.user.id
    app = await service.get_owned(db, Application, uid, application_id)
    if service.move_stage(db, app, body.stage, body.note, _today(body.today)):
        await db.commit()
        await db.refresh(app)
    return service.application_out(app)


@router.delete("/applications/{application_id}", status_code=204)
async def delete_application(
    application_id: uuid.UUID, request: Request, auth: CurrentUser, db: DB
) -> Response:
    uid = auth.user.id
    app = await service.get_owned(db, Application, uid, application_id)
    files = await db.scalar(
        select(func.count()).where(Attachment.user_id == uid, Attachment.application_id == app.id)
    )
    # A discovered posting that led here goes back to "saved".
    await db.execute(
        update(JobPosting)
        .where(JobPosting.user_id == uid, JobPosting.application_id == app.id)
        .values(application_id=None, status="saved")
    )
    # Events, attachments, links, referrals, reminders, and the offer cascade in the database.
    await db.execute(
        delete(Application).where(Application.user_id == uid, Application.id == app.id)
    )
    audit.record(
        db, request, "tracker.application_deleted", uid, application_id=str(app.id), files=files
    )
    await db.commit()
    return Response(status_code=204)


# --- timeline ------------------------------------------------------------------------------


@router.post("/applications/{application_id}/events", status_code=201)
async def create_event(
    application_id: uuid.UUID, body: EventIn, auth: CurrentUser, db: DB
) -> EventOut:
    uid = auth.user.id
    app = await service.get_owned(db, Application, uid, application_id)
    event = ApplicationEvent(
        user_id=uid,
        application_id=app.id,
        kind=body.kind,
        occurred_at=body.occurred_at or utcnow(),
        title=body.title,
        body=body.body,
        data=body.data.model_dump(exclude_defaults=True),
    )
    db.add(event)
    app.updated_at = utcnow()
    await db.commit()
    await db.refresh(event)
    return service.event_out(event, [])


async def _user_event(db: DB, uid: uuid.UUID, event_id: uuid.UUID) -> ApplicationEvent:
    event = await service.get_owned(db, ApplicationEvent, uid, event_id)
    if event.kind == "stage_change":
        raise ApiError(
            409, "read_only_event", "Stage changes are part of the history and can't be changed."
        )
    return event


@router.patch("/events/{event_id}")
async def patch_event(event_id: uuid.UUID, body: EventPatch, auth: CurrentUser, db: DB) -> EventOut:
    uid = auth.user.id
    event = await _user_event(db, uid, event_id)
    if body.occurred_at is not None:
        event.occurred_at = body.occurred_at
    if body.title is not None:
        event.title = body.title
    if body.body is not None:
        event.body = body.body
    if body.data is not None:
        event.data = body.data.model_dump(exclude_defaults=True)
    await db.commit()
    await db.refresh(event)
    by_event, _ = await service.files_by_event(db, uid, event.application_id)
    return service.event_out(event, by_event.get(event.id, []))


@router.delete("/events/{event_id}", status_code=204)
async def delete_event(event_id: uuid.UUID, auth: CurrentUser, db: DB) -> Response:
    uid = auth.user.id
    event = await _user_event(db, uid, event_id)
    # Keep the files: they stay on the application, just no longer tied to this event.
    await db.execute(
        update(Attachment)
        .where(Attachment.user_id == uid, Attachment.event_id == event.id)
        .values(event_id=None)
    )
    await db.delete(event)
    await db.commit()
    return Response(status_code=204)


# --- offers ----------------------------------------------------------------------------------


@router.put("/applications/{application_id}/offer")
async def put_offer(
    application_id: uuid.UUID, body: OfferIn, auth: CurrentUser, db: DB
) -> OfferOut:
    uid = auth.user.id
    app = await service.get_owned(db, Application, uid, application_id)
    offer = await db.scalar(
        select(Offer).where(Offer.user_id == uid, Offer.application_id == app.id)
    )
    if offer is None:
        offer = Offer(user_id=uid, application_id=app.id, **body.model_dump())
        db.add(offer)
    else:
        for key, value in body.model_dump().items():
            setattr(offer, key, value)
    await db.commit()
    await db.refresh(offer)
    return service.offer_out(offer, app)


@router.delete("/applications/{application_id}/offer", status_code=204)
async def delete_offer(application_id: uuid.UUID, auth: CurrentUser, db: DB) -> Response:
    uid = auth.user.id
    app = await service.get_owned(db, Application, uid, application_id)
    result = await db.execute(
        delete(Offer).where(Offer.user_id == uid, Offer.application_id == app.id)
    )
    if result.rowcount == 0:  # type: ignore[attr-defined]
        raise service.not_found()
    await db.commit()
    return Response(status_code=204)


@router.get("/offers")
async def list_offers(auth: CurrentUser, db: DB) -> list[OfferOut]:
    uid = auth.user.id
    rows = await db.execute(
        select(Offer, Application)
        .join(
            Application,
            (Application.id == Offer.application_id) & (Application.user_id == Offer.user_id),
        )
        .where(Offer.user_id == uid)
        .order_by(Offer.created_at)
    )
    return [service.offer_out(o, a) for o, a in rows.all()]


# --- summary ---------------------------------------------------------------------------------


@router.get("/summary")
async def summary(auth: CurrentUser, db: DB, today: date | None = None) -> TrackerSummary:
    uid = auth.user.id
    day = _today(today)
    stages = {s: 0 for s in STAGES}
    rows = await db.execute(
        select(Application.stage, func.count())
        .where(Application.user_id == uid)
        .group_by(Application.stage)
    )
    for stage, count in rows.all():
        stages[stage] = count
    items = await service.agenda_items(db, uid, until=day)
    return TrackerSummary(
        stages=stages,
        open_total=sum(stages[s] for s in OPEN_STAGES),
        overdue=sum(1 for i in items if i.due_on < day),
        due_today=sum(1 for i in items if i.due_on == day),
    )
