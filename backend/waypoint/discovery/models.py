# SPDX-License-Identifier: Apache-2.0
"""Discovery tables: sources, postings, sightings, filters, and the job queue."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from waypoint.db import Base, Timestamps, UUIDPk

SOURCE_KINDS = ("greenhouse", "lever", "ashby", "adzuna", "usajobs", "jsearch", "rss")
POSTING_STATUSES = ("new", "saved", "dismissed", "tracked")
JOB_STATUSES = ("queued", "running", "done", "failed")


def _one_of(column: str, values: tuple[str, ...]) -> CheckConstraint:
    return CheckConstraint(f"{column} IN ({', '.join(repr(v) for v in values)})", name=column)


class JobSource(UUIDPk, Timestamps, Base):
    __tablename__ = "job_sources"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_job_sources_user_id_id"),
        _one_of("kind", SOURCE_KINDS),
        CheckConstraint("interval_hours BETWEEN 1 AND 168", name="interval_hours"),
        Index("ix_job_sources_due", "enabled", "next_run_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    # API keys and similar, JSON-encoded then encrypted with the field cipher.
    secret_enc: Mapped[str | None] = mapped_column(Text)
    interval_hours: Mapped[int] = mapped_column(Integer, default=12, nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str] = mapped_column(String(10), default="never", nullable=False)
    last_error: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    last_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_new: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class JobPosting(UUIDPk, Timestamps, Base):
    __tablename__ = "job_postings"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_job_postings_user_id_id"),
        UniqueConstraint("user_id", "fingerprint", name="uq_job_postings_user_fingerprint"),
        ForeignKeyConstraint(
            ["user_id", "application_id"],
            ["applications.user_id", "applications.id"],
            name="fk_job_postings_application",
        ),
        _one_of("status", POSTING_STATUSES),
        Index("ix_job_postings_user_status", "user_id", "status"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    fingerprint: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    company: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    location: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    work_mode: Mapped[str | None] = mapped_column(String(10))
    pay_min_cents: Mapped[int | None] = mapped_column(BigInteger)
    pay_max_cents: Mapped[int | None] = mapped_column(BigInteger)
    pay_period: Mapped[str | None] = mapped_column(String(4))
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    shifts: Mapped[list[str]] = mapped_column(ARRAY(String(16)), default=list, nullable=False)
    clearance: Mapped[str | None] = mapped_column(String(16))
    employment_type: Mapped[str] = mapped_column(String(50), default="", nullable=False)
    # Untrusted text from the source; plain text only.
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    url: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closes_on: Mapped[date | None] = mapped_column(Date)
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(10), default="new", nullable=False)
    application_id: Mapped[uuid.UUID | None] = mapped_column()
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Set once a new-match alert has considered this posting (sent or filtered out).
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PostingSighting(UUIDPk, Base):
    """Where a posting was seen. One posting can come from several sources."""

    __tablename__ = "posting_sightings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "posting_id"],
            ["job_postings.user_id", "job_postings.id"],
            ondelete="CASCADE",
            name="fk_posting_sightings_posting",
        ),
        ForeignKeyConstraint(
            ["user_id", "source_id"],
            ["job_sources.user_id", "job_sources.id"],
            ondelete="CASCADE",
            name="fk_posting_sightings_source",
        ),
        # NULL source_id = imported by hand; external_id is then the URL or a hash.
        UniqueConstraint(
            "user_id",
            "source_id",
            "external_id",
            name="uq_posting_sightings_external",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_posting_sightings_posting", "posting_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    posting_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column()
    source_label: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    external_id: Mapped[str] = mapped_column(String(300), nullable=False)
    url: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class JobFilters(Timestamps, Base):
    __tablename__ = "job_filters"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    locations: Mapped[list[str]] = mapped_column(ARRAY(String(100)), default=list, nullable=False)
    home_lat: Mapped[float | None] = mapped_column(Float)
    home_lon: Mapped[float | None] = mapped_column(Float)
    radius_miles: Mapped[int | None] = mapped_column(Integer)
    remote_ok: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    hybrid_ok: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    onsite_ok: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    min_hourly_cents: Mapped[int | None] = mapped_column(BigInteger)
    target_hourly_cents: Mapped[int | None] = mapped_column(BigInteger)
    hide_unpaid_postings: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    shifts_wanted: Mapped[list[str]] = mapped_column(
        ARRAY(String(16)), default=list, nullable=False
    )
    shifts_excluded: Mapped[list[str]] = mapped_column(
        ARRAY(String(16)), default=list, nullable=False
    )
    exclude_keywords: Mapped[list[str]] = mapped_column(
        ARRAY(String(100)), default=list, nullable=False
    )
    require_keywords: Mapped[list[str]] = mapped_column(
        ARRAY(String(100)), default=list, nullable=False
    )
    clearance_held: Mapped[str] = mapped_column(String(16), default="none", nullable=False)
    preferred_companies: Mapped[list[str]] = mapped_column(
        ARRAY(String(200)), default=list, nullable=False
    )
    blocked_companies: Mapped[list[str]] = mapped_column(
        ARRAY(String(200)), default=list, nullable=False
    )


class QueuedJob(Base):
    """A background job. Claimed with SELECT ... FOR UPDATE SKIP LOCKED."""

    __tablename__ = "jobs"
    __table_args__ = (
        _one_of("status", JOB_STATUSES),
        Index("ix_jobs_ready", "status", "run_after"),
        # Only one queued/running job per dedupe key (e.g. one fetch per source).
        Index(
            "uq_jobs_active_dedupe",
            "dedupe_key",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running') AND dedupe_key IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    dedupe_key: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(10), default="queued", nullable=False)
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    last_error: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    locked_by: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
