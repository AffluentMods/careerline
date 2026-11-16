# SPDX-License-Identifier: Apache-2.0
"""Follow-ups for applications that have gone quiet.

CareerLine finds open applications with no activity for a while and drafts a short
follow-up email. Drafts only: nothing is ever sent. A draft may only repeat claims from
the materials the employer already has (resumes and cover letters written for that
application), and at most two follow-ups are suggested per application; after that,
silence is an answer.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from careerline import audit
from careerline.ai.jobs import allow_private_for
from careerline.ai.providers import AiError
from careerline.ai.service import client_for, structured, to_api_error
from careerline.auth.sessions import DB, CipherDep, CurrentUser, SettingsDep, csrf_protect
from careerline.errors import ApiError
from careerline.materials import build
from careerline.materials.models import Document
from careerline.models import VaultProfile
from careerline.security import contexts
from careerline.security.crypto import FieldCipher
from careerline.tracker.models import Application, ApplicationContact, ApplicationEvent, Contact
from careerline.vault.schemas import Strict

router = APIRouter(prefix="/api/v1/tracker", tags=["tracker"], dependencies=[Depends(csrf_protect)])

QUIET_STAGES = ("applied", "screening", "interviewing")
MAX_FOLLOW_UPS = 2
DEFAULT_QUIET_DAYS = 10
_NUMBER = re.compile(r"\d[\d,.]*%?")


class FollowUpItem(BaseModel):
    application_id: uuid.UUID
    title: str
    company: str
    stage: str
    last_activity: datetime
    days_quiet: int
    follow_ups_sent: int
    contact_name: str


class DraftIn(Strict):
    method: Literal["rules", "ai"] = "rules"


class DraftOut(BaseModel):
    subject: str
    body: str
    to: str
    number: int
    method: str
    warnings: list[str]


class SentIn(Strict):
    subject: str = Field(default="", max_length=200)
    body: str = Field(default="", max_length=5000)


class _AiDraft(BaseModel):
    subject: str = Field(max_length=200)
    body: str = Field(max_length=3000)


def _is_follow_up(event: ApplicationEvent) -> bool:
    return bool(event.data.get("follow_up"))


async def _activity(
    db: AsyncSession, uid: uuid.UUID, app_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[datetime | None, int]]:
    """Latest event time and follow-ups sent, per application."""
    out: dict[uuid.UUID, tuple[datetime | None, int]] = {}
    if not app_ids:
        return out
    rows = await db.execute(
        select(
            ApplicationEvent.application_id,
            func.max(ApplicationEvent.occurred_at),
            func.count().filter(ApplicationEvent.data["follow_up"].as_boolean().is_(True)),
        )
        .where(
            ApplicationEvent.user_id == uid,
            ApplicationEvent.application_id.in_(app_ids),
            # "Added as Applied" records when you entered it, not when you last heard back.
            ~and_(
                ApplicationEvent.kind == "stage_change",
                ApplicationEvent.data["from"].astext.is_(None),
            ),
        )
        .group_by(ApplicationEvent.application_id)
    )
    for app_id, latest, sent in rows.all():
        out[app_id] = (latest, int(sent or 0))
    return out


def _last_activity(app: Application, latest_event: datetime | None) -> datetime:
    """When something last happened: the latest event, or the day you applied."""
    candidates: list[datetime] = []
    if latest_event:
        candidates.append(latest_event)
    if app.applied_on:
        candidates.append(datetime.combine(app.applied_on, datetime.min.time(), tzinfo=UTC))
    return max(candidates) if candidates else app.stage_changed_at


async def _main_contact(
    db: AsyncSession, uid: uuid.UUID, app_id: uuid.UUID
) -> tuple[Contact | None, str]:
    rows = await db.execute(
        select(Contact, ApplicationContact.role)
        .join(
            ApplicationContact,
            (ApplicationContact.contact_id == Contact.id)
            & (ApplicationContact.user_id == Contact.user_id),
        )
        .where(ApplicationContact.user_id == uid, ApplicationContact.application_id == app_id)
    )
    ranked = sorted(
        rows.all(),
        key=lambda r: ("recruiter", "hiring_manager", "interviewer", "referrer", "other").index(
            r[1]
        ),
    )
    return (ranked[0][0], ranked[0][1]) if ranked else (None, "")


@router.get("/follow-ups")
async def list_follow_ups(
    auth: CurrentUser,
    db: DB,
    days: int = Query(default=DEFAULT_QUIET_DAYS, ge=1, le=90),
) -> list[FollowUpItem]:
    """Open applications with no activity for ``days`` days that can still be followed up."""
    uid = auth.user.id
    apps = list(
        await db.scalars(
            select(Application).where(
                Application.user_id == uid, Application.stage.in_(QUIET_STAGES)
            )
        )
    )
    activity = await _activity(db, uid, [a.id for a in apps])
    now = datetime.now(UTC)
    out: list[FollowUpItem] = []
    for app in apps:
        latest, sent = activity.get(app.id, (None, 0))
        last = _last_activity(app, latest)
        quiet = (now - last).days
        if quiet < days or sent >= MAX_FOLLOW_UPS:
            continue
        contact, _ = await _main_contact(db, uid, app.id)
        out.append(
            FollowUpItem(
                application_id=app.id,
                title=app.title,
                company=app.company,
                stage=app.stage,
                last_activity=last,
                days_quiet=quiet,
                follow_ups_sent=sent,
                contact_name=contact.name if contact else "",
            )
        )
    return sorted(out, key=lambda i: -i.days_quiet)


async def _app(db: AsyncSession, uid: uuid.UUID, app_id: uuid.UUID) -> Application:
    app = await db.scalar(
        select(Application).where(Application.user_id == uid, Application.id == app_id)
    )
    if app is None:
        raise ApiError(404, "not_found", "Not found.")
    return app


async def _materials(db: AsyncSession, uid: uuid.UUID, app: Application) -> list[str]:
    """What the employer already has: text from documents written for this application."""
    docs = await db.scalars(
        select(Document)
        .where(Document.user_id == uid, Document.application_id == app.id)
        .order_by(Document.exported_at.desc().nulls_last(), Document.created_at.desc())
        .limit(4)
    )
    lines: list[str] = []
    for d in docs:
        lines += [i["text"] for i in build.all_items(d.kind, d.content) if i.get("text")]
    return lines[:40]


def _first_name(name: str) -> str:
    return name.split()[0] if name.strip() else ""


def _applied_text(app: Application) -> str:
    d: date | None = app.applied_on
    return f"{d:%B} {d.day}" if d else ""


def _rules_draft(app: Application, contact_name: str, number: int) -> tuple[str, str]:
    role = app.title
    at = f" at {app.company}" if app.company else ""
    greeting = (
        f"Hi {_first_name(contact_name)},"
        if contact_name
        else (f"Hi {app.company} team," if app.company else "Hello,")
    )
    applied = _applied_text(app)
    when = f" on {applied}" if applied else ""
    if number == 1:
        subject = f"Following up: {role} application"
        body = (
            f"{greeting}\n\n"
            f"I applied for the {role} role{at}{when} and wanted to follow up. "
            "I'm still very interested and would be glad to share anything else that "
            "would help. Is there an update on the timeline?\n\n"
            "Thank you for your time,"
        )
    else:
        subject = f"Checking in: {role}"
        body = (
            f"{greeting}\n\n"
            f"I'm checking in once more about the {role} role{at}. If it's been filled, "
            "no problem at all, and I'd appreciate a quick note so I can plan. If it's "
            "still open, I remain very interested.\n\n"
            "Thanks again,"
        )
    return subject, body


def _unsupported_numbers(text: str, allowed: str) -> list[str]:
    have = {n.strip(".,") for n in _NUMBER.findall(allowed)}
    return [n.strip(".,") for n in _NUMBER.findall(text) if n.strip(".,") not in have]


async def _signature(db: AsyncSession, uid: uuid.UUID, display_name: str) -> str:
    profile = await db.get(VaultProfile, uid)
    return (profile.full_name if profile and profile.full_name else display_name).strip()


def _contact_email(cipher: FieldCipher, uid: uuid.UUID, contact: Contact | None) -> str:
    if contact is None or not contact.email_enc:
        return ""
    return cipher.decrypt(contact.email_enc, contexts.contact(uid, contact.id, "email"))


@router.post("/applications/{app_id}/follow-up/draft")
async def draft_follow_up(
    app_id: uuid.UUID,
    body: DraftIn,
    auth: CurrentUser,
    db: DB,
    cipher: CipherDep,
    settings: SettingsDep,
) -> DraftOut:
    """Draft a follow-up email. Nothing is sent or saved."""
    uid = auth.user.id
    app = await _app(db, uid, app_id)
    _, sent = (await _activity(db, uid, [app.id])).get(app.id, (None, 0))
    if sent >= MAX_FOLLOW_UPS:
        raise ApiError(
            409,
            "follow_up_limit",
            f"You've followed up {sent} times already. More would hurt more than help; "
            "it's time to focus on other applications.",
        )
    number = sent + 1
    contact, _ = await _main_contact(db, uid, app.id)
    contact_name = contact.name if contact else ""
    signature = await _signature(db, uid, auth.user.display_name)
    warnings: list[str] = []
    if body.method == "ai":
        materials = await _materials(db, uid, app)
        if not materials:
            warnings.append(
                "No resume or cover letter is linked to this application, so the draft "
                "makes no claims about your experience."
            )
        try:
            ai = await client_for(
                db,
                uid,
                cipher,
                allow_private=await allow_private_for(db, uid, settings.outbound_allow_private),
            )
            out = await structured(
                ai,
                feature="follow_up",
                prompt_name="follow_up",
                values={
                    "title": app.title,
                    "company": app.company or "(not given)",
                    "applied": _applied_text(app) or "(date not recorded)",
                    "contact": contact_name or "(none)",
                    "materials": "\n".join(f"- {m}" for m in materials) or "(none)",
                    "number": str(number),
                },
                schema_model=_AiDraft,
                max_tokens=1500,
                effort="low",
                subject={"application_id": str(app.id)},
            )
        except AiError as exc:
            raise to_api_error(exc) from None
        subject, text = out.subject.strip(), out.body.strip()
        allowed = " ".join([*materials, app.title, app.company, _applied_text(app)])
        extra = _unsupported_numbers(f"{subject} {text}", allowed)
        if extra:
            warnings.append(
                f"Mentions {', '.join(extra[:3])}, which isn't in what you sent them. "
                "Check it before sending."
            )
    else:
        subject, text = _rules_draft(app, contact_name, number)
    if signature and not text.rstrip().endswith(signature):
        text = f"{text.rstrip()}\n{signature}"
    return DraftOut(
        subject=subject[:200],
        body=text,
        to=_contact_email(cipher, uid, contact),
        number=number,
        method=body.method,
        warnings=warnings,
    )


@router.post("/applications/{app_id}/follow-up/sent", status_code=201)
async def log_follow_up(
    app_id: uuid.UUID, body: SentIn, request: Request, auth: CurrentUser, db: DB
) -> dict[str, Any]:
    """Record that you sent a follow-up (you send it yourself; CareerLine never does)."""
    uid = auth.user.id
    app = await _app(db, uid, app_id)
    _, sent = (await _activity(db, uid, [app.id])).get(app.id, (None, 0))
    event = ApplicationEvent(
        user_id=uid,
        application_id=app.id,
        kind="contact",
        occurred_at=datetime.now(UTC),
        title=f"Follow-up {sent + 1} sent" + (f": {body.subject}" if body.subject else ""),
        body=body.body,
        data={"follow_up": True},
    )
    db.add(event)
    await db.flush()
    audit.record(db, request, "tracker.follow_up_logged", uid, application_id=str(app.id))
    await db.commit()
    return {"event_id": str(event.id), "follow_ups_sent": sent + 1}
