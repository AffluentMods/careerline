# SPDX-License-Identifier: Apache-2.0
"""Notification settings and delivery log."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from waypoint.db import Base, UUIDPk
from waypoint.security import contexts

KINDS = ("digest", "matches", "test")
CHANNELS = ("email", "ntfy", "webhook")
WEBHOOK_KINDS = ("discord", "slack", "generic")


class NotificationSettings(Base):
    """One row per user. Channel addresses and tokens are encrypted (they act as secrets)."""

    __tablename__ = "notification_settings"
    __table_args__ = (
        CheckConstraint("digest_hour BETWEEN 0 AND 23", name="digest_hour_range"),
        CheckConstraint("match_min_score BETWEEN 0 AND 100", name="match_min_score_range"),
        CheckConstraint(
            "webhook_kind IN ('discord', 'slack', 'generic')", name="webhook_kind_allowed"
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)

    digest_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    digest_hour: Mapped[int] = mapped_column(Integer, default=8, nullable=False)
    last_digest_on: Mapped[date | None] = mapped_column(Date)
    matches_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    match_min_score: Mapped[int] = mapped_column(Integer, default=80, nullable=False)

    email_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    email_to_enc: Mapped[str | None] = mapped_column(Text)
    ntfy_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ntfy_url_enc: Mapped[str | None] = mapped_column(Text)
    ntfy_token_enc: Mapped[str | None] = mapped_column(Text)
    webhook_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    webhook_kind: Mapped[str] = mapped_column(String(10), default="discord", nullable=False)
    webhook_url_enc: Mapped[str | None] = mapped_column(Text)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class NotificationLog(UUIDPk, Base):
    __tablename__ = "notification_log"
    __table_args__ = (Index("ix_notification_log_user_created", "user_id", "created_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    channel: Mapped[str] = mapped_column(String(10), nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    title: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    detail: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


def setting_context(user_id: uuid.UUID, field: str) -> str:
    return contexts.notify(user_id, field)
