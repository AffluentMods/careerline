# SPDX-License-Identifier: Apache-2.0
"""Authentication API: registration, login, TOTP MFA, sessions, password change."""

from __future__ import annotations

import secrets
import uuid
from datetime import timedelta
from typing import NoReturn

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import delete, exists, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from waypoint import audit
from waypoint.auth.schemas import (
    ChangePasswordIn,
    CodeIn,
    LoginIn,
    LoginOut,
    MfaSetupOut,
    PasswordAndCodeIn,
    PasswordIn,
    ReauthIn,
    ReauthOut,
    RecoveryCodesOut,
    RegisterIn,
    SessionOut,
    SsoStatus,
    StatusOut,
    UserOut,
)
from waypoint.auth.sessions import (
    ACTIVE,
    DB,
    MFA_PENDING,
    REAUTH_WINDOW,
    CipherDep,
    CurrentUser,
    OptionalSession,
    PendingUser,
    SettingsDep,
    clear_session_cookie,
    csrf_protect,
    recently_authenticated,
    revoke_other_sessions,
    set_csrf_cookie,
    start_session,
    utcnow,
)
from waypoint.config import Settings
from waypoint.errors import ApiError
from waypoint.models import AuthSession, MfaRecoveryCode, User
from waypoint.security import contexts, passwords, totp
from waypoint.security.crypto import FieldCipher
from waypoint.security.ratelimit import check_and_hit

router = APIRouter(prefix="/api/v1/auth", tags=["auth"], dependencies=[Depends(csrf_protect)])

_REGISTER_LOCK_ID = 7_240_001
_MAX_MFA_FAILURES_PER_SESSION = 5


async def _fail(db: AsyncSession, status: int, code: str, message: str) -> NoReturn:
    """Commit audit entries and rate-limit hits, then raise."""
    await db.commit()
    raise ApiError(status, code, message)


def _mfa_ctx(user_id: uuid.UUID, pending: bool = False) -> str:
    return contexts.mfa(user_id, pending)


async def _check_second_factor(
    db: AsyncSession, cipher: FieldCipher, user: User, code: str
) -> str | None:
    """Verify a TOTP or recovery code. Returns the method used, or None.

    A successful code is consumed: the TOTP step is recorded, the recovery code is marked used.
    """
    if totp.looks_like_recovery_code(code):
        row = await db.scalar(
            select(MfaRecoveryCode).where(
                MfaRecoveryCode.user_id == user.id,
                MfaRecoveryCode.code_hash == totp.hash_recovery_code(code),
                MfaRecoveryCode.used_at.is_(None),
            )
        )
        if row is None:
            return None
        row.used_at = utcnow()
        return "recovery_code"
    if not user.mfa_secret_enc:
        return None
    secret = cipher.decrypt(user.mfa_secret_enc, _mfa_ctx(user.id))
    step = totp.verify(secret, code, user.mfa_last_step)
    if step is None:
        return None
    user.mfa_last_step = step
    return "totp"


async def _issue_recovery_codes(db: AsyncSession, user: User) -> list[str]:
    await db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id))
    codes = totp.new_recovery_codes()
    db.add_all(
        MfaRecoveryCode(user_id=user.id, code_hash=totp.hash_recovery_code(c)) for c in codes
    )
    return codes


# --- public ---------------------------------------------------------------------


@router.get("/status")
async def status(
    request: Request, response: Response, db: DB, settings: SettingsDep, session: OptionalSession
) -> StatusOut:
    if not request.cookies.get(settings.csrf_cookie):
        set_csrf_cookie(
            response, settings, session.csrf_token if session else secrets.token_urlsafe(32)
        )
    has_users = bool(await db.scalar(select(exists().select_from(User))))
    return StatusOut(
        has_users=has_users,
        registration_open=_registration_open(settings, has_users),
        session="none"
        if session is None
        else ("active" if session.state == ACTIVE else "mfa_pending"),
        password_login=settings.password_login,
        sso=SsoStatus(enabled=settings.oidc_enabled, label=settings.oidc_label),
        demo=settings.demo_mode,
    )


def _registration_open(settings: Settings, has_users: bool) -> bool:
    """Password registration: the first account, or later when the owner allows it."""
    if settings.demo_mode or not settings.password_login:
        return False
    return (not has_users) or settings.allow_registration


@router.post("/register", status_code=201)
async def register(
    body: RegisterIn,
    request: Request,
    response: Response,
    db: DB,
    settings: SettingsDep,
    session: OptionalSession,
) -> UserOut:
    # Serialise registrations so two people can't both become the first (owner) user.
    await db.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": _REGISTER_LOCK_ID})
    await check_and_hit(db, f"register:ip:{audit.client_ip(request)}", 5, timedelta(hours=1))

    has_users = bool(await db.scalar(select(exists().select_from(User))))
    if not _registration_open(settings, has_users):
        await _fail(db, 403, "registration_closed", "Registration is closed on this server.")
    if problem := passwords.password_problem(body.password, body.email):
        await _fail(db, 422, "weak_password", problem)
    if await db.scalar(select(exists().where(User.email == body.email))):
        await _fail(db, 409, "email_taken", "An account with that email already exists.")

    user = User(
        email=body.email,
        display_name=body.display_name,
        password_hash=await passwords.hash_password(body.password),
        is_owner=not has_users,
    )
    db.add(user)
    await db.flush()
    if session is not None:
        await db.delete(session)
    await start_session(db, request, response, settings, user, ACTIVE)
    audit.record(db, request, "auth.register", user.id, owner=user.is_owner)
    await db.commit()
    return UserOut.model_validate(user)


@router.post("/login")
async def login(
    body: LoginIn,
    request: Request,
    response: Response,
    db: DB,
    settings: SettingsDep,
    session: OptionalSession,
) -> LoginOut:
    if not settings.password_login:
        raise ApiError(403, "password_login_off", "Sign in with single sign-on on this server.")
    await check_and_hit(db, f"login:ip:{audit.client_ip(request)}", 30, timedelta(minutes=5))
    await check_and_hit(db, f"login:email:{body.email}", 10, timedelta(minutes=15))

    user = await db.scalar(select(User).where(User.email == body.email))
    ok = await passwords.verify_password(user.password_hash if user else None, body.password)
    if user is None or not ok or not user.is_active:
        audit.record(
            db, request, "auth.login_failed", user.id if user else None, reason="bad_credentials"
        )
        await _fail(db, 401, "invalid_credentials", "Email or password is incorrect.")

    if user.password_hash and passwords.needs_rehash(user.password_hash):
        user.password_hash = await passwords.hash_password(body.password)
    # Never reuse a session that existed before login (prevents session fixation).
    if session is not None:
        await db.delete(session)

    if user.mfa_enabled:
        await start_session(db, request, response, settings, user, MFA_PENDING)
        audit.record(db, request, "auth.login_mfa_challenge", user.id)
        await db.commit()
        return LoginOut(status="mfa_required")

    await start_session(db, request, response, settings, user, ACTIVE)
    audit.record(db, request, "auth.login", user.id, mfa=False)
    await db.commit()
    return LoginOut(status="ok", user=UserOut.model_validate(user))


@router.post("/mfa/verify")
async def mfa_verify(
    body: CodeIn,
    request: Request,
    response: Response,
    db: DB,
    settings: SettingsDep,
    cipher: CipherDep,
    auth: PendingUser,
) -> LoginOut:
    user, pending = auth.user, auth.session
    await check_and_hit(db, f"mfa:user:{user.id}", 10, timedelta(minutes=15))

    method = await _check_second_factor(db, cipher, user, body.code)
    if method is None:
        pending.mfa_failures += 1
        audit.record(db, request, "auth.mfa_failed", user.id)
        if pending.mfa_failures >= _MAX_MFA_FAILURES_PER_SESSION:
            await db.delete(pending)
            await _fail(db, 401, "mfa_locked", "Too many wrong codes. Please sign in again.")
        await _fail(db, 401, "invalid_code", "That code didn't work. Try again.")

    await db.delete(pending)
    await start_session(db, request, response, settings, user, ACTIVE)
    remaining = None
    if method == "recovery_code":
        remaining = await db.scalar(
            select(func.count())
            .select_from(MfaRecoveryCode)
            .where(MfaRecoveryCode.user_id == user.id, MfaRecoveryCode.used_at.is_(None))
        )
    audit.record(
        db, request, "auth.login", user.id, mfa=True, method=method, recovery_left=remaining
    )
    await db.commit()
    return LoginOut(status="ok", user=UserOut.model_validate(user))


@router.post("/logout", status_code=204)
async def logout(
    request: Request, response: Response, db: DB, settings: SettingsDep, session: OptionalSession
) -> None:
    if session is not None:
        audit.record(db, request, "auth.logout", session.user_id)
        await db.delete(session)
        await db.commit()
    clear_session_cookie(response, settings)


# --- signed in ------------------------------------------------------------------


@router.get("/me")
async def me(auth: CurrentUser) -> UserOut:
    return UserOut.model_validate(auth.user)


@router.post("/reauth")
async def reauth(body: ReauthIn, request: Request, db: DB, auth: CurrentUser) -> ReauthOut:
    """Confirm the password again before sensitive actions (exports, imports)."""
    user = auth.user
    await check_and_hit(db, f"reauth:user:{user.id}", 10, timedelta(minutes=15))
    if user.password_hash is None:
        await _fail(
            db, 409, "sso_reauth", "Your account uses single sign-on. Sign in again to confirm."
        )
    if not await passwords.verify_password(user.password_hash, body.password):
        audit.record(db, request, "auth.reauth_failed", user.id)
        await _fail(db, 401, "invalid_credentials", "That password is incorrect.")
    auth.session.reauth_at = utcnow()
    audit.record(db, request, "auth.reauth", user.id)
    await db.commit()
    return ReauthOut(until=auth.session.reauth_at + REAUTH_WINDOW)


@router.get("/sessions")
async def list_sessions(db: DB, auth: CurrentUser) -> list[SessionOut]:
    rows = await db.scalars(
        select(AuthSession)
        .where(
            AuthSession.user_id == auth.user.id,
            AuthSession.state == ACTIVE,
            AuthSession.expires_at > utcnow(),
        )
        .order_by(AuthSession.last_seen_at.desc())
    )
    return [
        SessionOut(
            id=s.id,
            created_at=s.created_at,
            last_seen_at=s.last_seen_at,
            ip=s.ip,
            user_agent=s.user_agent,
            current=s.id == auth.session.id,
        )
        for s in rows
    ]


@router.delete("/sessions/{session_id}", status_code=204)
async def revoke_session(
    session_id: uuid.UUID,
    request: Request,
    response: Response,
    db: DB,
    settings: SettingsDep,
    auth: CurrentUser,
) -> None:
    target = await db.scalar(
        select(AuthSession).where(AuthSession.id == session_id, AuthSession.user_id == auth.user.id)
    )
    if target is None:
        raise ApiError(404, "not_found", "Session not found.")
    if target.id == auth.session.id:
        clear_session_cookie(response, settings)
    await db.delete(target)
    audit.record(db, request, "auth.session_revoked", auth.user.id, session_id=str(session_id))
    await db.commit()


@router.post("/password", status_code=204)
async def change_password(
    body: ChangePasswordIn, request: Request, db: DB, auth: CurrentUser
) -> None:
    user = auth.user
    await check_and_hit(db, f"password:user:{user.id}", 10, timedelta(minutes=15))
    if user.password_hash is None:
        # Single sign-on account setting its first password: a fresh sign-in stands in
        # for the current password.
        if not recently_authenticated(auth.session):
            await _fail(db, 403, "reauth_required", "Sign in again, then set your password.")
    elif not await passwords.verify_password(user.password_hash, body.current_password):
        audit.record(db, request, "auth.password_change_failed", user.id)
        await _fail(db, 401, "invalid_credentials", "Current password is incorrect.")
    if problem := passwords.password_problem(body.new_password, user.email):
        await _fail(db, 422, "weak_password", problem)
    user.password_hash = await passwords.hash_password(body.new_password)
    user.password_changed_at = utcnow()
    await revoke_other_sessions(db, user.id, keep=auth.session)
    audit.record(db, request, "auth.password_changed", user.id)
    await db.commit()


@router.post("/mfa/setup")
async def mfa_setup(
    body: PasswordIn, request: Request, db: DB, cipher: CipherDep, auth: CurrentUser
) -> MfaSetupOut:
    user = auth.user
    await check_and_hit(db, f"password:user:{user.id}", 10, timedelta(minutes=15))
    if user.mfa_enabled:
        await _fail(db, 409, "mfa_already_enabled", "Two-factor authentication is already on.")
    if not await passwords.verify_password(user.password_hash, body.password):
        await _fail(db, 401, "invalid_credentials", "Password is incorrect.")
    secret = totp.new_secret()
    user.mfa_pending_secret_enc = cipher.encrypt(secret, _mfa_ctx(user.id, pending=True))
    await db.commit()
    return MfaSetupOut(secret=secret, otpauth_uri=totp.provisioning_uri(secret, user.email))


@router.post("/mfa/enable")
async def mfa_enable(
    body: CodeIn, request: Request, db: DB, cipher: CipherDep, auth: CurrentUser
) -> RecoveryCodesOut:
    user = auth.user
    await check_and_hit(db, f"mfa:user:{user.id}", 10, timedelta(minutes=15))
    if user.mfa_enabled:
        await _fail(db, 409, "mfa_already_enabled", "Two-factor authentication is already on.")
    if not user.mfa_pending_secret_enc:
        await _fail(db, 409, "mfa_setup_required", "Start setup first.")
    secret = cipher.decrypt(user.mfa_pending_secret_enc, _mfa_ctx(user.id, pending=True))
    step = totp.verify(secret, body.code, None)
    if step is None:
        await _fail(db, 401, "invalid_code", "That code didn't work. Check your device's clock.")

    user.mfa_secret_enc = cipher.encrypt(secret, _mfa_ctx(user.id))
    user.mfa_pending_secret_enc = None
    user.mfa_last_step = step
    user.mfa_enabled = True
    codes = await _issue_recovery_codes(db, user)
    await revoke_other_sessions(db, user.id, keep=auth.session)
    audit.record(db, request, "auth.mfa_enabled", user.id)
    await db.commit()
    return RecoveryCodesOut(recovery_codes=codes)


async def _confirm_password_and_code(
    db: AsyncSession, request: Request, cipher: FieldCipher, user: User, body: PasswordAndCodeIn
) -> None:
    await check_and_hit(db, f"mfa:user:{user.id}", 10, timedelta(minutes=15))
    if not user.mfa_enabled:
        await _fail(db, 409, "mfa_not_enabled", "Two-factor authentication is off.")
    if not await passwords.verify_password(user.password_hash, body.password):
        await _fail(db, 401, "invalid_credentials", "Password is incorrect.")
    if await _check_second_factor(db, cipher, user, body.code) is None:
        audit.record(db, request, "auth.mfa_failed", user.id)
        await _fail(db, 401, "invalid_code", "That code didn't work.")


@router.post("/mfa/disable", status_code=204)
async def mfa_disable(
    body: PasswordAndCodeIn, request: Request, db: DB, cipher: CipherDep, auth: CurrentUser
) -> None:
    user = auth.user
    await _confirm_password_and_code(db, request, cipher, user, body)
    user.mfa_enabled = False
    user.mfa_secret_enc = None
    user.mfa_pending_secret_enc = None
    user.mfa_last_step = None
    await db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id))
    audit.record(db, request, "auth.mfa_disabled", user.id)
    await db.commit()


@router.post("/mfa/recovery-codes")
async def regenerate_recovery_codes(
    body: PasswordAndCodeIn, request: Request, db: DB, cipher: CipherDep, auth: CurrentUser
) -> RecoveryCodesOut:
    user = auth.user
    await _confirm_password_and_code(db, request, cipher, user, body)
    codes = await _issue_recovery_codes(db, user)
    audit.record(db, request, "auth.recovery_codes_regenerated", user.id)
    await db.commit()
    return RecoveryCodesOut(recovery_codes=codes)
