# SPDX-License-Identifier: Apache-2.0
"""Request/response models for the tracker, plus the pay maths for offers."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from careerline.vault.schemas import (
    Brief,
    Long,
    Name,
    Short,
    Strict,
    Tiny,
    Url,
    _email_or_blank,
)

Stage = Literal[
    "saved", "applied", "screening", "interviewing", "offer", "accepted", "rejected", "withdrawn"
]
UserEventKind = Literal["note", "sent", "interview", "contact", "other"]
ContactRole = Literal["recruiter", "hiring_manager", "interviewer", "referrer", "other"]
ReferralStatus = Literal["to_ask", "asked", "agreed", "submitted", "declined"]
ReminderKind = Literal["follow_up", "deadline", "interview", "task"]
WorkMode = Literal["onsite", "hybrid", "remote"]
PayPeriod = Literal["hour", "year"]
Priority = Literal["low", "normal", "high"]

Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
Cents = Annotated[int, Field(ge=0, le=100_000_000_000)]
Description = Annotated[str, StringConstraints(max_length=50_000)]
Body = Annotated[str, StringConstraints(strip_whitespace=True, max_length=10_000)]
Email = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=320),
    AfterValidator(_email_or_blank),
]

WEEKS_PER_YEAR = 52


def annual_cents(base_cents: int, period: str, hours_per_week: int) -> int:
    if period == "year":
        return base_cents
    return base_cents * hours_per_week * WEEKS_PER_YEAR


def hourly_cents(base_cents: int, period: str, hours_per_week: int) -> int:
    if period == "hour":
        return base_cents
    hours = hours_per_week * WEEKS_PER_YEAR
    return (base_cents + hours // 2) // hours  # round half up


def _check_pay(low: int | None, high: int | None, period: str | None) -> None:
    if low is not None and high is not None and low > high:
        raise ValueError("pay_min_cents must not be above pay_max_cents")
    if (low is not None or high is not None) and period is None:
        raise ValueError("pay_period is required when a pay amount is set")


# --- applications ------------------------------------------------------------------------


class ApplicationFields(Strict):
    title: Name
    company: Short = ""
    location: Short = ""
    work_mode: WorkMode | None = None
    job_url: Url = ""
    source: Brief = ""
    pay_min_cents: Cents | None = None
    pay_max_cents: Cents | None = None
    pay_period: PayPeriod | None = None
    currency: Currency = "USD"
    description: Description = ""
    applied_on: date | None = None
    deadline: date | None = None
    priority: Priority = "normal"


class ApplicationIn(ApplicationFields):
    stage: Stage = "saved"

    @model_validator(mode="after")
    def _pay(self) -> ApplicationIn:
        _check_pay(self.pay_min_cents, self.pay_max_cents, self.pay_period)
        return self


class ApplicationPatch(Strict):
    title: Name | None = None
    company: Short | None = None
    location: Short | None = None
    work_mode: WorkMode | None = None
    job_url: Url | None = None
    source: Brief | None = None
    pay_min_cents: Cents | None = None
    pay_max_cents: Cents | None = None
    pay_period: PayPeriod | None = None
    currency: Currency | None = None
    description: Description | None = None
    applied_on: date | None = None
    deadline: date | None = None
    priority: Priority | None = None


# Fields where an explicit null in a PATCH means "clear it".
NULLABLE_APPLICATION_FIELDS = frozenset(
    {"work_mode", "pay_min_cents", "pay_max_cents", "pay_period", "applied_on", "deadline"}
)


class StageIn(Strict):
    stage: Stage
    note: Body = ""
    # The browser's local date, used to fill in applied_on when moving to Applied.
    today: date | None = None


class DueItem(BaseModel):
    due_on: date
    due_time: time | None
    title: str
    source: str


class ApplicationOut(BaseModel):
    id: uuid.UUID
    title: str
    company: str
    location: str
    work_mode: str | None
    job_url: str
    source: str
    pay_min_cents: int | None
    pay_max_cents: int | None
    pay_period: str | None
    currency: str
    description: str
    stage: str
    stage_changed_at: datetime
    applied_on: date | None
    deadline: date | None
    priority: str
    created_at: datetime
    updated_at: datetime


class ApplicationSummary(BaseModel):
    id: uuid.UUID
    title: str
    company: str
    location: str
    work_mode: str | None
    pay_min_cents: int | None
    pay_max_cents: int | None
    pay_period: str | None
    currency: str
    stage: str
    stage_changed_at: datetime
    applied_on: date | None
    deadline: date | None
    priority: str
    updated_at: datetime
    last_activity_at: datetime
    next_due: DueItem | None
    contact_count: int
    has_offer: bool


# --- timeline -------------------------------------------------------------------------------


class EventData(Strict):
    materials: Short = ""  # what was sent, e.g. "Resume v3 + cover letter"
    interview_format: Literal["phone", "video", "onsite", "other"] | None = None
    with_whom: Short = ""
    channel: Literal["email", "phone", "message", "in_person", "other"] | None = None


class EventIn(Strict):
    kind: UserEventKind
    occurred_at: datetime | None = None
    title: Short = ""
    body: Body = ""
    data: EventData = Field(default_factory=EventData)


class EventPatch(Strict):
    occurred_at: datetime | None = None
    title: Short | None = None
    body: Body | None = None
    data: EventData | None = None


class AttachmentOut(BaseModel):
    id: uuid.UUID
    application_id: uuid.UUID
    event_id: uuid.UUID | None
    filename: str
    mime: str
    size_bytes: int
    created_at: datetime


class EventOut(BaseModel):
    id: uuid.UUID
    application_id: uuid.UUID
    kind: str
    occurred_at: datetime
    title: str
    body: str
    data: dict[str, object]
    attachments: list[AttachmentOut]
    created_at: datetime
    updated_at: datetime


# --- contacts & referrals -----------------------------------------------------------------


class ContactIn(Strict):
    name: Name
    company: Short = ""
    title: Short = ""
    email: Email = ""
    phone: Tiny = ""
    linkedin_url: Url = ""
    notes: Long = ""


class ContactPatch(Strict):
    name: Name | None = None
    company: Short | None = None
    title: Short | None = None
    email: Email | None = None
    phone: Tiny | None = None
    linkedin_url: Url | None = None
    notes: Long | None = None


class ContactLink(BaseModel):
    application_id: uuid.UUID
    role: str
    title: str
    company: str
    stage: str


class ContactOut(BaseModel):
    id: uuid.UUID
    name: str
    company: str
    title: str
    email: str
    phone: str
    linkedin_url: str
    notes: str
    links: list[ContactLink]
    created_at: datetime
    updated_at: datetime


class LinkIn(Strict):
    role: ContactRole


class LinkedContactOut(BaseModel):
    contact: ContactOut
    role: str


class ReferralIn(Strict):
    contact_id: uuid.UUID
    status: ReferralStatus = "to_ask"
    asked_on: date | None = None
    referred_on: date | None = None
    follow_up_on: date | None = None
    notes: Long = ""


class ReferralPatch(Strict):
    status: ReferralStatus | None = None
    asked_on: date | None = None
    referred_on: date | None = None
    follow_up_on: date | None = None
    notes: Long | None = None


class ReferralOut(BaseModel):
    id: uuid.UUID
    application_id: uuid.UUID
    contact_id: uuid.UUID
    contact_name: str
    status: str
    asked_on: date | None
    referred_on: date | None
    follow_up_on: date | None
    notes: str
    created_at: datetime
    updated_at: datetime


# --- reminders & agenda ---------------------------------------------------------------------


class ReminderIn(Strict):
    title: Name
    kind: ReminderKind = "task"
    due_on: date
    due_time: time | None = None
    application_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    notes: Long = ""


class ReminderPatch(Strict):
    title: Name | None = None
    kind: ReminderKind | None = None
    due_on: date | None = None
    due_time: time | None = None
    application_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    notes: Long | None = None
    done: bool | None = None


class ReminderOut(BaseModel):
    id: uuid.UUID
    title: str
    kind: str
    due_on: date
    due_time: time | None
    application_id: uuid.UUID | None
    contact_id: uuid.UUID | None
    notes: str
    done_at: datetime | None
    created_at: datetime
    updated_at: datetime


AgendaSource = Literal["reminder", "application_deadline", "offer_deadline", "referral_follow_up"]


class AgendaItem(BaseModel):
    source: AgendaSource
    id: uuid.UUID  # the reminder, application, offer, or referral id
    title: str
    kind: str
    due_on: date
    due_time: time | None
    application_id: uuid.UUID | None
    application_title: str
    company: str
    contact_id: uuid.UUID | None
    contact_name: str


class TrackerSummary(BaseModel):
    stages: dict[str, int]
    open_total: int
    overdue: int
    due_today: int


# --- offers ------------------------------------------------------------------------------


class OfferIn(Strict):
    currency: Currency = "USD"
    base_cents: Annotated[int, Field(gt=0, le=100_000_000_000)]
    base_period: PayPeriod
    hours_per_week: Annotated[int, Field(ge=1, le=100)] = 40
    bonus_cents: Cents | None = None
    bonus_notes: Long = ""
    equity_notes: Long = ""
    pto_days: Annotated[int, Field(ge=0, le=365)] | None = None
    schedule: Short = ""
    commute_minutes: Annotated[int, Field(ge=0, le=600)] | None = None
    growth_notes: Long = ""
    benefits_notes: Long = ""
    deadline: date | None = None


class OfferOut(OfferIn):
    model_config = ConfigDict(extra="ignore")

    id: uuid.UUID
    application_id: uuid.UUID
    application_title: str
    company: str
    stage: str
    annual_cents: int
    hourly_cents: int
    total_annual_cents: int
    updated_at: datetime


class ApplicationDetailOut(BaseModel):
    application: ApplicationOut
    events: list[EventOut]
    attachments: list[AttachmentOut]  # files not tied to an event
    contacts: list[LinkedContactOut]
    referrals: list[ReferralOut]
    reminders: list[ReminderOut]
    offer: OfferOut | None
