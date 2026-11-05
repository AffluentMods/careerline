# SPDX-License-Identifier: Apache-2.0
"""AI tables: per-user provider settings, a log of every model call, and fit scores."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from waypoint.db import Base, Timestamps, UUIDPk

PROVIDERS = ("none", "anthropic", "openai", "ollama")


class AiSettings(Timestamps, Base):
    __tablename__ = "ai_settings"
    __table_args__ = (
        CheckConstraint(
            "provider IN (" + ", ".join(f"'{p}'" for p in PROVIDERS) + ")", name="provider"
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    provider: Mapped[str] = mapped_column(String(16), default="none", nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    api_key_enc: Mapped[str | None] = mapped_column(Text)
    model_scoring: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    model_writing: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    model_chat: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    # Strip emails, phone numbers, and street addresses before text goes to a hosted model.
    redact_pii: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    auto_score: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    monthly_budget_cents: Mapped[int | None] = mapped_column(Integer)


class AiRun(UUIDPk, Base):
    """One model call. Prompts and outputs are not stored, only what's needed for cost and audit."""

    __tablename__ = "ai_runs"
    __table_args__ = (Index("ix_ai_runs_user_created", "user_id", "created_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    feature: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt_name: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # ok / error / invalid / refused
    error: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Estimated cost in micro-dollars (1e-6 USD); NULL when the model's price is unknown.
    cost_micros: Mapped[int | None] = mapped_column(BigInteger)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    subject: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class JobScore(UUIDPk, Base):
    __tablename__ = "job_scores"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "posting_id"],
            ["job_postings.user_id", "job_postings.id"],
            ondelete="CASCADE",
            name="fk_job_scores_posting",
        ),
        UniqueConstraint("user_id", "posting_id", name="uq_job_scores_posting"),
        CheckConstraint("score BETWEEN 0 AND 100", name="score_range"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    posting_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    method: Mapped[str] = mapped_column(String(8), nullable=False)  # rules / ai
    model: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    summary: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    # Hash of the verified vault facts used, so a score can be marked stale when they change.
    vault_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
