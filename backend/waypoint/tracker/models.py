# SPDX-License-Identifier: Apache-2.0
"""Tracker tables.

As with the vault, every reference between user-owned rows is a composite
foreign key on ``(user_id, ...)``, so the database rejects cross-user links.
Optional links (reminder -> contact, attachment -> event) use the default
NO ACTION rule; the service layer clears them before deleting the target.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from waypoint.db import Base, Timestamps, UUIDPk

STAGES = (
    "saved",
    "applied",
    "screening",
    "interviewing",
    "offer",
    "accepted",
    "rejected",
    "withdrawn",
)
OPEN_STAGES = STAGES[:5]
EVENT_KINDS = ("stage_change", "note", "sent", "interview", "contact", "other")
CONTACT_ROLES = ("recruiter", "hiring_manager", "interviewer", "referrer", "other")
REFERRAL_STATUSES = ("to_ask", "asked", "agreed", "submitted", "declined")
REMINDER_KINDS = ("follow_up", "deadline", "interview", "task")
WORK_MODES = ("onsite", "hybrid", "remote")
PAY_PERIODS = ("hour", "year")
PRIORITIES = ("low", "normal", "high")


def _one_of(column: str, values: tuple[str, ...], nullable: bool = False) -> CheckConstraint:
    allowed = ", ".join(f"'{v}'" for v in values)
    expr = f"{column} IN ({allowed})"
    if nullable:
        expr = f"{column} IS NULL OR {expr}"
    return CheckConstraint(expr, name=column)


def _user_fk() -> Any:
    return mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)


def _app_fk(table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["user_id", "application_id"],
        ["applications.user_id", "applications.id"],
        ondelete="CASCADE",
        name=f"fk_{table}_application",
    )


class Application(UUIDPk, Timestamps, Base):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_applications_user_id_id"),
        _one_of("stage", STAGES),
        _one_of("work_mode", WORK_MODES, nullable=True),
        _one_of("pay_period", PAY_PERIODS, nullable=True),
        _one_of("priority", PRIORITIES),
        CheckConstraint(
            "pay_min_cents IS NULL OR pay_max_cents IS NULL OR pay_min_cents <= pay_max_cents",
            name="pay_range",
        ),
        Index("ix_applications_user_stage", "user_id", "stage"),
    )

    user_id: Mapped[uuid.UUID] = _user_fk()
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    company: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    location: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    work_mode: Mapped[str | None] = mapped_column(String(10))
    job_url: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    source: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    pay_min_cents: Mapped[int | None] = mapped_column(BigInteger)
    pay_max_cents: Mapped[int | None] = mapped_column(BigInteger)
    pay_period: Mapped[str | None] = mapped_column(String(4))
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    # Pasted job description: untrusted text, always rendered as plain text.
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    stage: Mapped[str] = mapped_column(String(16), default="saved", nullable=False)
    stage_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    applied_on: Mapped[date | None] = mapped_column(Date)
    deadline: Mapped[date | None] = mapped_column(Date)
    priority: Mapped[str] = mapped_column(String(6), default="normal", nullable=False)


class ApplicationEvent(UUIDPk, Timestamps, Base):
    __tablename__ = "application_events"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_application_events_user_id_id"),
        _app_fk("application_events"),
        _one_of("kind", EVENT_KINDS),
        Index("ix_application_events_app", "application_id", "occurred_at"),
    )

    user_id: Mapped[uuid.UUID] = _user_fk()
    application_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    title: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)


class Attachment(UUIDPk, Base):
    __tablename__ = "attachments"
    __table_args__ = (
        _app_fk("attachments"),
        ForeignKeyConstraint(
            ["user_id", "event_id"],
            ["application_events.user_id", "application_events.id"],
            name="fk_attachments_event",
        ),
        Index("ix_attachments_app", "application_id"),
        Index("ix_attachments_user", "user_id"),
    )

    user_id: Mapped[uuid.UUID] = _user_fk()
    application_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    event_id: Mapped[uuid.UUID | None] = mapped_column()
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # AES-GCM encrypted file contents (FieldCipher.encrypt_bytes).
    content_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Contact(UUIDPk, Timestamps, Base):
    __tablename__ = "contacts"
    __table_args__ = (UniqueConstraint("user_id", "id", name="uq_contacts_user_id_id"),)

    user_id: Mapped[uuid.UUID] = _user_fk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    company: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    title: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    email_enc: Mapped[str | None] = mapped_column(Text)
    phone_enc: Mapped[str | None] = mapped_column(Text)
    linkedin_url: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)


class ApplicationContact(Base):
    __tablename__ = "application_contacts"
    __table_args__ = (
        _app_fk("application_contacts"),
        ForeignKeyConstraint(
            ["user_id", "contact_id"],
            ["contacts.user_id", "contacts.id"],
            ondelete="CASCADE",
            name="fk_application_contacts_contact",
        ),
        _one_of("role", CONTACT_ROLES),
    )

    application_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    contact_id: Mapped[uuid.UUID] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[uuid.UUID] = _user_fk()
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Referral(UUIDPk, Timestamps, Base):
    __tablename__ = "referrals"
    __table_args__ = (
        _app_fk("referrals"),
        ForeignKeyConstraint(
            ["user_id", "contact_id"],
            ["contacts.user_id", "contacts.id"],
            ondelete="CASCADE",
            name="fk_referrals_contact",
        ),
        UniqueConstraint("application_id", "contact_id", name="uq_referrals_app_contact"),
        _one_of("status", REFERRAL_STATUSES),
    )

    user_id: Mapped[uuid.UUID] = _user_fk()
    application_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    contact_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(10), default="to_ask", nullable=False)
    asked_on: Mapped[date | None] = mapped_column(Date)
    referred_on: Mapped[date | None] = mapped_column(Date)
    follow_up_on: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)


class Reminder(UUIDPk, Timestamps, Base):
    __tablename__ = "reminders"
    __table_args__ = (
        _app_fk("reminders"),
        ForeignKeyConstraint(
            ["user_id", "contact_id"],
            ["contacts.user_id", "contacts.id"],
            name="fk_reminders_contact",
        ),
        _one_of("kind", REMINDER_KINDS),
        Index("ix_reminders_user_due", "user_id", "due_on"),
    )

    user_id: Mapped[uuid.UUID] = _user_fk()
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), default="task", nullable=False)
    # A local calendar date and optional time, as the user entered them (no time zone).
    due_on: Mapped[date] = mapped_column(Date, nullable=False)
    due_time: Mapped[time | None] = mapped_column(Time)
    application_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)


class Offer(UUIDPk, Timestamps, Base):
    __tablename__ = "offers"
    __table_args__ = (
        _app_fk("offers"),
        UniqueConstraint("application_id", name="uq_offers_application_id"),
        _one_of("base_period", PAY_PERIODS),
        CheckConstraint("hours_per_week BETWEEN 1 AND 100", name="hours_per_week"),
    )

    user_id: Mapped[uuid.UUID] = _user_fk()
    application_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    base_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    base_period: Mapped[str] = mapped_column(String(4), nullable=False)
    hours_per_week: Mapped[int] = mapped_column(Integer, default=40, nullable=False)
    bonus_cents: Mapped[int | None] = mapped_column(BigInteger)
    bonus_notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    equity_notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    pto_days: Mapped[int | None] = mapped_column(Integer)
    schedule: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    commute_minutes: Mapped[int | None] = mapped_column(Integer)
    growth_notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    benefits_notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    deadline: Mapped[date | None] = mapped_column(Date)
