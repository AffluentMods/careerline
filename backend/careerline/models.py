# SPDX-License-Identifier: Apache-2.0
"""Database models.

Rule for every future user-owned table: a non-null ``user_id`` foreign key with
``ondelete="CASCADE"``, and every query filtered by the current user's id.
"""

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
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from careerline.db import Base, Timestamps, UUIDPk


class User(UUIDPk, Timestamps, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # None for accounts that only sign in through single sign-on.
    password_hash: Mapped[str | None] = mapped_column(String(255))
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # TOTP secrets are encrypted with the field cipher (see security/crypto.py).
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mfa_secret_enc: Mapped[str | None] = mapped_column(Text)
    mfa_pending_secret_enc: Mapped[str | None] = mapped_column(Text)
    # Last accepted TOTP time step, so a code can't be replayed.
    mfa_last_step: Mapped[int | None] = mapped_column(BigInteger)

    @property
    def has_password(self) -> bool:
        return self.password_hash is not None


class AuthSession(UUIDPk, Base):
    __tablename__ = "auth_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # SHA-256 of the cookie token. The raw token is never stored.
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True, nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    # "active" or "mfa_pending"
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    mfa_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Last time the user proved who they are in this session (password or single sign-on).
    # Exports and imports need this to be recent.
    reauth_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # "password" or "oidc"
    method: Mapped[str] = mapped_column(
        String(8), default="password", server_default="password", nullable=False
    )


class MfaRecoveryCode(UUIDPk, Base):
    __tablename__ = "mfa_recovery_codes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # SHA-256 of the normalised code. Codes have ~60 bits of entropy, so a fast hash is enough.
    code_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    event: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(256))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True, nullable=False
    )


class RateLimitHit(Base):
    __tablename__ = "rate_limit_hits"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(200), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True, nullable=False
    )


# --- Career Vault (Phase 2) ------------------------------------------------------------
#
# Cross-user isolation is enforced by the database as well as by queries: every table
# that can reference another user-owned row does so through a composite foreign key on
# (user_id, id), so a row can only ever point at rows owned by the same user.

ENTRY_KINDS = (
    "role",
    "responsibility",
    "achievement",
    "project",
    "certification",
    "education",
    "skill",
    "link",
)


class VaultProfile(Timestamps, Base):
    __tablename__ = "vault_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    full_name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    headline: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    location_text: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Contact details are encrypted with the field cipher.
    email_enc: Mapped[str | None] = mapped_column(Text)
    phone_enc: Mapped[str | None] = mapped_column(Text)
    address_enc: Mapped[str | None] = mapped_column(Text)


class ResumeImport(UUIDPk, Base):
    __tablename__ = "resume_imports"
    __table_args__ = (UniqueConstraint("user_id", "id", name="uq_resume_imports_user_id_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # Extracted text and profile suggestions, encrypted. The original file is never stored.
    text_enc: Mapped[str] = mapped_column(Text, nullable=False)
    suggestions_enc: Mapped[str] = mapped_column(Text, nullable=False)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class VaultEntry(UUIDPk, Timestamps, Base):
    __tablename__ = "vault_entries"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_vault_entries_user_id_id"),
        ForeignKeyConstraint(
            ["user_id", "parent_id"],
            ["vault_entries.user_id", "vault_entries.id"],
            ondelete="CASCADE",
            name="fk_vault_entries_parent",
        ),
        CheckConstraint("kind IN (" + ", ".join(f"'{k}'" for k in ENTRY_KINDS) + ")", name="kind"),
        CheckConstraint("source IN ('manual', 'import', 'api')", name="source"),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="not_own_parent"),
        Index("ix_vault_entries_user_kind", "user_id", "kind"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column()
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Only verified entries (whose parents are also verified) may be used by the AI.
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(10), default="manual", nullable=False)
    import_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resume_imports.id", ondelete="SET NULL"), index=True
    )
    # "YYYY" or "YYYY-MM" derived from the entry's dates, for newest-first ordering.
    sort_date: Mapped[str | None] = mapped_column(String(7))


class Story(UUIDPk, Timestamps, Base):
    __tablename__ = "stories"
    __table_args__ = (UniqueConstraint("user_id", "id", name="uq_stories_user_id_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    situation: Mapped[str] = mapped_column(Text, default="", nullable=False)
    task: Mapped[str] = mapped_column(Text, default="", nullable=False)
    action: Mapped[str] = mapped_column(Text, default="", nullable=False)
    result: Mapped[str] = mapped_column(Text, default="", nullable=False)
    reflection: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(50)), default=list, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StoryEntry(Base):
    __tablename__ = "story_entries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "story_id"],
            ["stories.user_id", "stories.id"],
            ondelete="CASCADE",
            name="fk_story_entries_story",
        ),
        ForeignKeyConstraint(
            ["user_id", "entry_id"],
            ["vault_entries.user_id", "vault_entries.id"],
            ondelete="CASCADE",
            name="fk_story_entries_entry",
        ),
    )

    story_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    entry_id: Mapped[uuid.UUID] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )


class OidcIdentity(UUIDPk, Base):
    """A single sign-on account (issuer + subject) linked to a CareerLine user."""

    __tablename__ = "oidc_identities"
    __table_args__ = (UniqueConstraint("issuer", "subject", name="uq_oidc_identities_subject"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    issuer: Mapped[str] = mapped_column(String(500), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(320), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
