# SPDX-License-Identifier: Apache-2.0
"""Server-side sessions, cookies, CSRF, and the auth dependencies routes use."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from waypoint.audit import client_ip, user_agent
from waypoint.config import Settings
from waypoint.db import get_db
from waypoint.errors import ApiError
from waypoint.models import AuthSession, User
from waypoint.security.crypto import FieldCipher

ACTIVE = "active"
MFA_PENDING = "mfa_pending"
CSRF_HEADER = "x-csrf-token"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_TOUCH_INTERVAL = timedelta(minutes=1)


def utcnow() -> datetime:
    return datetime.now(UTC)


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_cipher(request: Request) -> FieldCipher:
    cipher: FieldCipher = request.app.state.cipher
    return cipher


DB = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
CipherDep = Annotated[FieldCipher, Depends(get_cipher)]


# --- cookies -----------------------------------------------------------------


def set_csrf_cookie(response: Response, settings: Settings, value: str) -> None:
    # Readable by the SPA's JavaScript on purpose (double-submit pattern).
    response.set_cookie(
        settings.csrf_cookie,
        value,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )


def set_session_cookie(response: Response, settings: Settings, token: str, max_age: int) -> None:
    response.set_cookie(
        settings.session_cookie,
        token,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        settings.session_cookie, path="/", secure=settings.cookie_secure, httponly=True
    )
    set_csrf_cookie(response, settings, secrets.token_urlsafe(32))


# --- session lifecycle ---------------------------------------------------------


async def start_session(
    db: AsyncSession,
    request: Request,
    response: Response,
    settings: Settings,
    user: User,
    state: str,
    method: str = "password",
) -> AuthSession:
    """Create a session, set its cookies, and return it. The caller commits.

    A fully signed-in session counts as freshly re-authenticated (see ``require_reauth``)."""
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    ttl = (
        timedelta(minutes=settings.mfa_pending_minutes)
        if state == MFA_PENDING
        else timedelta(hours=settings.session_ttl_hours)
    )
    session = AuthSession(
        user_id=user.id,
        token_hash=hash_token(token),
        csrf_token=csrf,
        state=state,
        ip=client_ip(request),
        user_agent=user_agent(request),
        expires_at=utcnow() + ttl,
        method=method,
        reauth_at=utcnow() if state == ACTIVE else None,
    )
    db.add(session)
    set_session_cookie(response, settings, token, int(ttl.total_seconds()))
    set_csrf_cookie(response, settings, csrf)
    return session


async def revoke_other_sessions(
    db: AsyncSession, user_id: object, keep: AuthSession | None
) -> None:
    stmt = delete(AuthSession).where(AuthSession.user_id == user_id)
    if keep is not None:
        stmt = stmt.where(AuthSession.id != keep.id)
    await db.execute(stmt)


# --- dependencies ----------------------------------------------------------------


async def get_optional_session(
    request: Request, db: DB, settings: SettingsDep
) -> AuthSession | None:
    token = request.cookies.get(settings.session_cookie)
    if not token or len(token) > 128:
        return None
    session = await db.scalar(
        select(AuthSession).where(AuthSession.token_hash == hash_token(token))
    )
    if session is None:
        return None
    now = utcnow()
    idle_expired = session.state == ACTIVE and (
        session.last_seen_at + timedelta(minutes=settings.session_idle_minutes) <= now
    )
    if session.expires_at <= now or idle_expired:
        await db.delete(session)
        await db.commit()
        return None
    if now - session.last_seen_at > _TOUCH_INTERVAL:
        session.last_seen_at = now
        await db.commit()
    return session


OptionalSession = Annotated[AuthSession | None, Depends(get_optional_session)]


async def csrf_protect(request: Request, settings: SettingsDep, session: OptionalSession) -> None:
    """Require a matching CSRF token on every state-changing request.

    The header must equal the CSRF cookie (double submit) and, when a session
    exists, the token bound to that session.
    """
    if request.method in _SAFE_METHODS:
        return
    header = request.headers.get(CSRF_HEADER, "")
    cookie = request.cookies.get(settings.csrf_cookie, "")
    ok = bool(header) and bool(cookie) and hmac.compare_digest(header, cookie)
    if ok and session is not None:
        ok = hmac.compare_digest(header, session.csrf_token)
    if not ok:
        raise ApiError(403, "csrf_failed", "Security check failed. Reload the page and try again.")


@dataclass
class Authenticated:
    user: User
    session: AuthSession


async def _load_user(db: AsyncSession, session: AuthSession) -> User:
    user = await db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise ApiError(401, "unauthenticated", "Please sign in.")
    return user


async def require_user(db: DB, session: OptionalSession) -> Authenticated:
    if session is None:
        raise ApiError(401, "unauthenticated", "Please sign in.")
    if session.state != ACTIVE:
        raise ApiError(401, "mfa_required", "Enter your authentication code to finish signing in.")
    return Authenticated(user=await _load_user(db, session), session=session)


async def require_mfa_pending(db: DB, session: OptionalSession) -> Authenticated:
    if session is None or session.state != MFA_PENDING:
        raise ApiError(401, "unauthenticated", "Your sign-in expired. Please sign in again.")
    return Authenticated(user=await _load_user(db, session), session=session)


CurrentUser = Annotated[Authenticated, Depends(require_user)]

# How long a password (or single sign-on) confirmation unlocks exports and imports.
REAUTH_WINDOW = timedelta(minutes=10)


def recently_authenticated(session: AuthSession) -> bool:
    return session.reauth_at is not None and utcnow() - session.reauth_at <= REAUTH_WINDOW


def require_reauth(auth: Authenticated) -> None:
    if not recently_authenticated(auth.session):
        raise ApiError(403, "reauth_required", "Confirm it's you first.")


PendingUser = Annotated[Authenticated, Depends(require_mfa_pending)]
