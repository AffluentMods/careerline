# SPDX-License-Identifier: Apache-2.0
"""Using the REST API with a full-access API key.

A key with the ``full`` scope works like a signed-in session on ``/api/v1/...``: it sends
``Authorization: Bearer cl_...`` instead of the session cookie. It reaches every part of the
app except what would let a leaked key take over or empty the account:

* sign-in and security (``/api/v1/auth``: password, two-factor, sessions, single sign-on),
* API key management (``/api/v1/api-tokens``),
* full data export and import (``/api/v1/data/export``, ``/api/v1/data/import``).

Those stay behind the browser session. A bearer request carries no cookies the browser adds
on its own, so CSRF doesn't apply to it; a request that also carries a session cookie is
treated as a browser request and goes through the normal session and CSRF checks.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from careerline import audit
from careerline.api_access import tokens
from careerline.errors import ApiError
from careerline.models import AuthSession
from careerline.security.ratelimit import check_and_hit

BLOCKED_PREFIXES = (
    "/api/v1/auth",
    "/api/v1/api-tokens",
    "/api/v1/data/export",
    "/api/v1/data/import",
)
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CALLS_PER_WINDOW = 600
CALL_WINDOW = timedelta(minutes=10)
WRITES_PER_HOUR = 300
FAILED_AUTH_PER_IP = 30


def blocked(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in BLOCKED_PREFIXES)


def key_request(request: Request, session_cookie: str) -> str | None:
    """The API key a request authenticates with, if it uses one instead of a session."""
    if request.cookies.get(session_cookie):
        return None
    return tokens.bearer(request.headers.get("authorization"))


async def authenticate(db: AsyncSession, request: Request, raw: str) -> tokens.KeyAuth:
    """Check a full-access key for a REST request, applying the key's rate limits."""
    key = await tokens.authenticate(db, raw)
    if key is None:
        await check_and_hit(
            db,
            f"mcp_auth_fail:{audit.client_ip(request) or 'unknown'}",
            FAILED_AUTH_PER_IP,
            timedelta(minutes=15),
        )
        await db.commit()
        raise ApiError(401, "unauthenticated", "That API key isn't valid.")
    if not key.full:
        raise ApiError(
            403,
            "full_access_required",
            "This API key can only be used through the MCP endpoint. Create a key with full "
            "access to use the REST API.",
        )
    if blocked(request.url.path):
        raise ApiError(
            403,
            "not_for_api_keys",
            "API keys can't change sign-in security or API keys, or export or import all "
            "data. Do this in the app.",
        )
    # Shared with the MCP endpoint, so one key has one budget however it's used.
    await check_and_hit(db, f"mcp:{key.token.id}", CALLS_PER_WINDOW, CALL_WINDOW)
    request.state.api_token_id = str(key.token.id)
    if request.method not in _SAFE_METHODS:
        await check_and_hit(db, f"mcp_write:{key.token.id}", WRITES_PER_HOUR, timedelta(hours=1))
        audit.record(
            db, request, "api.request", key.user.id, method=request.method, path=request.url.path
        )
    await db.commit()
    return key


def stand_in_session(key: tokens.KeyAuth) -> AuthSession:
    """A session object for routes that expect one. Never added to the database.

    It has never re-authenticated, so anything guarded by ``require_reauth`` is refused."""
    now = datetime.now(UTC)
    return AuthSession(
        id=key.token.id,
        user_id=key.user.id,
        token_hash=b"",
        csrf_token=secrets.token_urlsafe(16),
        state="active",
        mfa_failures=0,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(minutes=5),
        reauth_at=None,
        method="api_key",
    )
