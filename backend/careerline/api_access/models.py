# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, LargeBinary, String, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from careerline.db import Base, UUIDPk

# What a key may do. Nothing else (account, security, export, settings) is reachable with one.
SCOPES = ("read", "draft", "propose")


class ApiToken(UUIDPk, Base):
    """A personal API key for the MCP endpoint. Only its SHA-256 hash is stored."""

    __tablename__ = "api_tokens"
    __table_args__ = (
        CheckConstraint("cardinality(scopes) > 0", name="scopes_not_empty"),
        CheckConstraint(
            "scopes <@ ARRAY[" + ", ".join(f"'{s}'" for s in SCOPES) + "]::varchar[]",
            name="scopes_known",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True, nullable=False)
    # The first characters of the key, so the user can tell keys apart. Not enough to use.
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String(16)), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The assistant conversation that holds this key's proposed changes (created on first use).
    conversation_id: Mapped[uuid.UUID | None] = mapped_column()
