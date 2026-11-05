# SPDX-License-Identifier: Apache-2.0
"""Creating and checking API keys.

A key is ``cl_`` plus 43 random URL-safe characters (256 bits). Only its SHA-256 hash is
stored, like session tokens, so a database leak doesn't hand out working keys.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from careerline.api_access.models import ApiToken
from careerline.models import User

PREFIX = "cl_"
_MAX_LENGTH = 100
_TOUCH_INTERVAL = timedelta(minutes=1)


def new_token() -> str:
    return PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def display_prefix(token: str) -> str:
    return token[: len(PREFIX) + 5]


@dataclass
class KeyAuth:
    user: User
    token: ApiToken

    def can(self, scope: str) -> bool:
        return scope in self.token.scopes


def bearer(authorization: str | None) -> str | None:
    """The key from an ``Authorization: Bearer cl_...`` header, or None."""
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    value = value.strip()
    if scheme.lower() != "bearer" or not value.startswith(PREFIX) or len(value) > _MAX_LENGTH:
        return None
    return value


async def authenticate(db: AsyncSession, raw: str) -> KeyAuth | None:
    """Look up a key. Returns None if it's unknown, expired, or its user is disabled."""
    token = await db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(raw)))
    if token is None:
        return None
    now = datetime.now(UTC)
    if token.expires_at is not None and token.expires_at <= now:
        return None
    user = await db.get(User, token.user_id)
    if user is None or not user.is_active:
        return None
    if token.last_used_at is None or now - token.last_used_at > _TOUCH_INTERVAL:
        token.last_used_at = now
    return KeyAuth(user=user, token=token)
