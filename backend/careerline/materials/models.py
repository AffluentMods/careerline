# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from careerline.db import Base, Timestamps, UUIDPk


class Document(UUIDPk, Timestamps, Base):
    """A generated resume or cover letter.

    ``content`` holds the structure; every bullet or paragraph lists the vault facts it
    came from and a fact-check status. The target posting/application is referenced by
    id and snapshotted (title, company), so documents survive if the job is deleted.
    Contact details are never stored here: they are read from the profile at export.
    """

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("kind IN ('resume', 'cover_letter')", name="kind"),
        CheckConstraint("method IN ('rules', 'ai')", name="method"),
        Index("ix_documents_user_created", "user_id", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    is_master: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    posting_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    application_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    target_title: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    target_company: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    model: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    vault_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
