# SPDX-License-Identifier: Apache-2.0
"""Notification settings, test sends, and the delivery log."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Annotated, Literal
from zoneinfo import available_timezones

from fastapi import APIRouter, Depends, Request
from pydantic import AfterValidator, BaseModel, Field, StringConstraints
from sqlalchemy import select

from waypoint import audit
from waypoint.ai.jobs import allow_private_for
from waypoint.auth.schemas import normalise_email
from waypoint.auth.sessions import DB, CipherDep, CurrentUser, SettingsDep, csrf_protect
from waypoint.errors import ApiError
from waypoint.notify import senders, service
from waypoint.notify.models import NotificationLog, NotificationSettings, setting_context
from waypoint.security.crypto import FieldCipher
from waypoint.security.ratelimit import check_and_hit
from waypoint.vault.schemas import Strict

router = APIRouter(
    prefix="/api/v1/notifications", tags=["notifications"], dependencies=[Depends(csrf_protect)]
)

Channel = Literal["email", "ntfy", "webhook"]


def _tz(value: str) -> str:
    if value not in available_timezones():
        raise ValueError("unknown time zone")
    return value


Url = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]


def _email_or_blank(value: str) -> str:
    return normalise_email(value) if value.strip() else ""


Email = Annotated[str, StringConstraints(max_length=320), AfterValidator(_email_or_blank)]


class NotifyIn(Strict):
    timezone: Annotated[str, StringConstraints(max_length=64), AfterValidator(_tz)] = "UTC"
    digest_enabled: bool = False
    digest_hour: int = Field(default=8, ge=0, le=23)
    matches_enabled: bool = False
    match_min_score: int = Field(default=80, ge=0, le=100)
    email_enabled: bool = False
    # For every secret-ish field: None keeps what's stored, "" removes it.
    email_to: Email | None = None
    ntfy_enabled: bool = False
    ntfy_url: Url | None = None
    # Tokens go in a header: printable ASCII only.
    ntfy_token: (
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, max_length=200, pattern=r"^[!-~]*$"),
        ]
        | None
    ) = None
    webhook_enabled: bool = False
    webhook_kind: Literal["discord", "slack", "generic"] = "discord"
    webhook_url: Url | None = None


class NotifyOut(BaseModel):
    timezone: str
    digest_enabled: bool
    digest_hour: int
    matches_enabled: bool
    match_min_score: int
    email_enabled: bool
    email_to: str
    email_available: bool
    ntfy_enabled: bool
    ntfy_url: str
    has_ntfy_token: bool
    webhook_enabled: bool
    webhook_kind: str
    webhook_hint: str
    private_targets_allowed: bool
    demo: bool


class TestIn(Strict):
    channel: Channel


class TestOut(BaseModel):
    ok: bool
    error: str | None


class LogOut(BaseModel):
    id: uuid.UUID
    kind: str
    channel: str
    ok: bool
    title: str
    detail: str
    created_at: datetime


async def _out(
    db: DB, request: Request, row: NotificationSettings, cipher: FieldCipher
) -> NotifyOut:
    settings = request.app.state.settings
    ch = service.secrets_of(cipher, row)
    return NotifyOut(
        timezone=row.timezone,
        digest_enabled=row.digest_enabled,
        digest_hour=row.digest_hour,
        matches_enabled=row.matches_enabled,
        match_min_score=row.match_min_score,
        email_enabled=row.email_enabled,
        email_to=ch.email_to,
        email_available=settings.smtp_enabled,
        ntfy_enabled=row.ntfy_enabled,
        ntfy_url=ch.ntfy_url,
        has_ntfy_token=bool(ch.ntfy_token),
        webhook_enabled=row.webhook_enabled,
        webhook_kind=row.webhook_kind,
        webhook_hint=senders.mask_url(ch.webhook_url) if ch.webhook_url else "",
        private_targets_allowed=await allow_private_for(
            db, row.user_id, settings.outbound_allow_private
        ),
        demo=settings.demo_mode,
    )


@router.get("/settings")
async def get_settings(request: Request, auth: CurrentUser, db: DB, cipher: CipherDep) -> NotifyOut:
    row = await db.get(NotificationSettings, auth.user.id)
    if row is None:
        row = await service.get_row(db, auth.user.id)
        await db.rollback()  # defaults only; nothing is stored until the user saves
    return await _out(db, request, row, cipher)


@router.put("/settings")
async def put_settings(
    body: NotifyIn,
    request: Request,
    auth: CurrentUser,
    db: DB,
    cipher: CipherDep,
    settings: SettingsDep,
) -> NotifyOut:
    uid = auth.user.id
    if settings.demo_mode and (body.email_enabled or body.ntfy_enabled or body.webhook_enabled):
        raise ApiError(403, "demo_mode", "Notifications can't be sent in demo mode.")
    row = await service.get_row(db, uid)

    def put(field: str, value: str | None) -> None:
        if value is None:
            return
        enc = cipher.encrypt(value, setting_context(uid, field)) if value else None
        setattr(row, f"{field}_enc", enc)

    try:
        ntfy_url = senders.check_ntfy_url(body.ntfy_url) if body.ntfy_url else body.ntfy_url
        webhook_url = (
            senders.check_webhook_url(body.webhook_kind, body.webhook_url)
            if body.webhook_url
            else body.webhook_url
        )
        if (
            body.webhook_url is None
            and row.webhook_url_enc
            and body.webhook_kind != row.webhook_kind
        ):
            # The stored address must still suit the (new) kind.
            stored = service.secrets_of(cipher, row).webhook_url
            senders.check_webhook_url(body.webhook_kind, stored)
    except ValueError as exc:
        raise ApiError(422, "invalid_address", str(exc)) from None

    put("email_to", body.email_to)
    put("ntfy_url", ntfy_url)
    put("ntfy_token", body.ntfy_token)
    put("webhook_url", webhook_url)
    turned_on = body.digest_enabled and not row.digest_enabled
    row.timezone = body.timezone
    row.digest_enabled = body.digest_enabled
    row.digest_hour = body.digest_hour
    row.matches_enabled = body.matches_enabled
    row.match_min_score = body.match_min_score
    row.email_enabled = body.email_enabled
    row.ntfy_enabled = body.ntfy_enabled
    row.webhook_enabled = body.webhook_enabled
    row.webhook_kind = body.webhook_kind
    if turned_on:
        # Start tomorrow if today's digest hour has already passed.
        local = service.local_now(row)
        row.last_digest_on = local.date() if local.hour > body.digest_hour else None
    audit.record(db, request, "notifications.updated", uid)
    await db.commit()
    return await _out(db, request, row, cipher)


@router.post("/test")
async def send_test(
    body: TestIn,
    request: Request,
    auth: CurrentUser,
    db: DB,
    cipher: CipherDep,
    settings: SettingsDep,
) -> TestOut:
    uid = auth.user.id
    if settings.demo_mode:
        raise ApiError(403, "demo_mode", "Notifications can't be sent in demo mode.")
    await check_and_hit(db, f"notify-test:{uid}", 10, timedelta(hours=1))
    row = await db.get(NotificationSettings, uid)
    if row is None:
        raise ApiError(409, "not_configured", "Save your notification settings first.")
    fetcher = request.app.state.fetcher_factory()
    fetcher.allow_private = await allow_private_for(db, uid, settings.outbound_allow_private)
    results = await service.deliver(
        db, row, cipher, settings, fetcher, service.test_message(settings), [body.channel]
    )
    await db.commit()
    return TestOut(ok=results[body.channel] is None, error=results[body.channel])


@router.get("/log")
async def delivery_log(auth: CurrentUser, db: DB) -> list[LogOut]:
    rows = await db.scalars(
        select(NotificationLog)
        .where(NotificationLog.user_id == auth.user.id)
        .order_by(NotificationLog.created_at.desc())
        .limit(50)
    )
    return [LogOut.model_validate(r, from_attributes=True) for r in rows]
