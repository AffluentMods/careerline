# SPDX-License-Identifier: Apache-2.0
"""Managing API keys: list, create (after re-entering the password), revoke."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, StringConstraints, field_validator
from sqlalchemy import delete, func, select

from careerline import audit
from careerline.api_access import tokens
from careerline.api_access.models import SCOPES, ApiToken
from careerline.assistant.models import PendingAction
from careerline.auth.sessions import DB, CurrentUser, csrf_protect, require_reauth
from careerline.errors import ApiError
from careerline.security.ratelimit import check_and_hit
from careerline.vault.schemas import Strict

router = APIRouter(
    prefix="/api/v1/api-tokens", tags=["api-access"], dependencies=[Depends(csrf_protect)]
)

MAX_KEYS = 20
Scope = Literal["read", "draft", "propose", "full"]


class TokenIn(Strict):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    scopes: list[Scope] = Field(min_length=1, max_length=len(SCOPES))
    # None means the key never expires.
    expires_in_days: Literal[7, 30, 90, 365] | None = 90

    @field_validator("scopes")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        return [s for s in SCOPES if s in value]


class TokenOut(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    expired: bool
    conversation_id: uuid.UUID | None
    pending_proposals: int


class CreatedTokenOut(TokenOut):
    # Shown once. The server keeps only a hash.
    token: str


def _out(t: ApiToken, pending: int, now: datetime) -> TokenOut:
    return TokenOut(
        id=t.id,
        name=t.name,
        prefix=t.prefix,
        scopes=list(t.scopes),
        created_at=t.created_at,
        last_used_at=t.last_used_at,
        expires_at=t.expires_at,
        expired=t.expires_at is not None and t.expires_at <= now,
        conversation_id=t.conversation_id,
        pending_proposals=pending,
    )


@router.get("")
async def list_tokens(auth: CurrentUser, db: DB) -> list[TokenOut]:
    uid = auth.user.id
    rows = list(
        await db.scalars(
            select(ApiToken).where(ApiToken.user_id == uid).order_by(ApiToken.created_at.desc())
        )
    )
    conv_ids = [t.conversation_id for t in rows if t.conversation_id]
    pending: dict[uuid.UUID, int] = {}
    if conv_ids:
        counts = await db.execute(
            select(PendingAction.conversation_id, func.count())
            .where(
                PendingAction.user_id == uid,
                PendingAction.conversation_id.in_(conv_ids),
                PendingAction.status == "pending",
            )
            .group_by(PendingAction.conversation_id)
        )
        pending = {cid: n for cid, n in counts.all()}
    now = datetime.now(UTC)
    return [
        _out(t, pending.get(t.conversation_id, 0) if t.conversation_id else 0, now) for t in rows
    ]


@router.post("", status_code=201)
async def create_token(
    body: TokenIn, request: Request, auth: CurrentUser, db: DB
) -> CreatedTokenOut:
    require_reauth(auth)
    uid = auth.user.id
    await check_and_hit(db, f"api_token_create:{uid}", 10, timedelta(hours=1))
    await db.commit()
    count = await db.scalar(select(func.count()).where(ApiToken.user_id == uid))
    if (count or 0) >= MAX_KEYS:
        raise ApiError(
            409, "too_many_keys", f"You can have up to {MAX_KEYS} keys. Revoke one first."
        )
    raw = tokens.new_token()
    now = datetime.now(UTC)
    token = ApiToken(
        user_id=uid,
        name=body.name,
        token_hash=tokens.hash_token(raw),
        prefix=tokens.display_prefix(raw),
        scopes=body.scopes,
        expires_at=now + timedelta(days=body.expires_in_days) if body.expires_in_days else None,
    )
    db.add(token)
    await db.flush()
    audit.record(
        db,
        request,
        "api_token.created",
        uid,
        token_id=str(token.id),
        name=body.name,
        scopes=body.scopes,
        expires_in_days=body.expires_in_days,
    )
    await db.commit()
    await db.refresh(token)
    return CreatedTokenOut(**_out(token, 0, now).model_dump(), token=raw)


class TokenPatch(Strict):
    scopes: list[Scope] = Field(min_length=1, max_length=len(SCOPES))

    @field_validator("scopes")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        return [s for s in SCOPES if s in value]


@router.patch("/{token_id}")
async def update_token(
    token_id: uuid.UUID, body: TokenPatch, request: Request, auth: CurrentUser, db: DB
) -> TokenOut:
    """Change what an existing key may do. Like creating one, this needs the password again."""
    require_reauth(auth)
    uid = auth.user.id
    token = await db.scalar(
        select(ApiToken).where(ApiToken.user_id == uid, ApiToken.id == token_id)
    )
    if token is None:
        raise ApiError(404, "not_found", "Not found.")
    before = list(token.scopes)
    token.scopes = list(body.scopes)
    audit.record(
        db,
        request,
        "api_token.updated",
        uid,
        token_id=str(token.id),
        name=token.name,
        scopes_before=before,
        scopes=body.scopes,
    )
    await db.commit()
    pending = 0
    if token.conversation_id:
        pending = int(
            await db.scalar(
                select(func.count()).where(
                    PendingAction.user_id == uid,
                    PendingAction.conversation_id == token.conversation_id,
                    PendingAction.status == "pending",
                )
            )
            or 0
        )
    return _out(token, pending, datetime.now(UTC))


@router.delete("/{token_id}", status_code=204)
async def revoke_token(
    token_id: uuid.UUID, request: Request, auth: CurrentUser, db: DB
) -> Response:
    uid = auth.user.id
    result = await db.execute(
        delete(ApiToken)
        .where(ApiToken.user_id == uid, ApiToken.id == token_id)
        .returning(ApiToken.name)
    )
    name = result.scalar_one_or_none()
    if name is None:
        raise ApiError(404, "not_found", "Not found.")
    audit.record(db, request, "api_token.revoked", uid, token_id=str(token_id), name=name)
    await db.commit()
    return Response(status_code=204)
