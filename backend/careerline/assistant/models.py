# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
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

from careerline.db import Base, Timestamps, UUIDPk


def _user() -> Any:
    return mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)


class Conversation(UUIDPk, Timestamps, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_conversations_user_id_id"),
        CheckConstraint("source IN ('chat', 'api')", name="source"),
    )

    user_id: Mapped[uuid.UUID] = _user()
    title: Mapped[str] = mapped_column(String(200), default="New conversation", nullable=False)
    # "chat": a conversation with the built-in assistant. "api": changes proposed through an
    # API key, shown here so the user can confirm or reject them. It can't be chatted in.
    source: Mapped[str] = mapped_column(
        String(8), default="chat", server_default="chat", nullable=False
    )


class ChatMessage(UUIDPk, Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "conversation_id"],
            ["conversations.user_id", "conversations.id"],
            ondelete="CASCADE",
            name="fk_chat_messages_conversation",
        ),
        CheckConstraint("role IN ('user', 'assistant', 'tool')", name="role"),
        Index("ix_chat_messages_conv_seq", "conversation_id", "seq"),
    )

    user_id: Mapped[uuid.UUID] = _user()
    conversation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    tool_call_id: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    tool_name: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    # Provider-native assistant content (e.g. Claude's blocks), sent back unchanged.
    native: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PendingAction(UUIDPk, Base):
    """A change the assistant proposed. Nothing happens until the user confirms it."""

    __tablename__ = "pending_actions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "conversation_id"],
            ["conversations.user_id", "conversations.id"],
            ondelete="CASCADE",
            name="fk_pending_actions_conversation",
        ),
        CheckConstraint("status IN ('pending', 'confirmed', 'rejected', 'failed')", name="status"),
    )

    user_id: Mapped[uuid.UUID] = _user()
    conversation_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    tool_call_id: Mapped[str] = mapped_column(String(100), nullable=False)
    tool: Mapped[str] = mapped_column(String(64), nullable=False)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="pending", nullable=False)
    result: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InterviewPrep(UUIDPk, Timestamps, Base):
    __tablename__ = "interview_preps"
    __table_args__ = (CheckConstraint("method IN ('rules', 'ai')", name="method"),)

    user_id: Mapped[uuid.UUID] = _user()
    posting_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    application_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    target_title: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    target_company: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    # Snapshot of the job text (untrusted) for mock interviews.
    target_description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    questions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)


class MockSession(UUIDPk, Timestamps, Base):
    __tablename__ = "mock_sessions"
    __table_args__ = (CheckConstraint("status IN ('active', 'finished')", name="status"),)

    user_id: Mapped[uuid.UUID] = _user()
    prep_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    target_title: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    target_company: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    target_description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    max_questions: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="active", nullable=False)
    turns: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
