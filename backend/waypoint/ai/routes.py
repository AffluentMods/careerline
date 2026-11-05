# SPDX-License-Identifier: Apache-2.0
"""AI API: provider settings, connection test, usage and cost, on-demand scoring."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import AfterValidator, BaseModel, Field, StringConstraints
from sqlalchemy import func, select

from waypoint import audit
from waypoint.ai.jobs import allow_private_for
from waypoint.ai.models import AiRun, AiSettings
from waypoint.ai.providers import AiError
from waypoint.ai.scoring import score_detail, score_posting
from waypoint.ai.service import (
    DEFAULT_BASE_URL,
    DEFAULT_MODELS,
    client_for,
    encrypt_key,
    model_for,
    month_spend_micros,
    structured,
    to_api_error,
)
from waypoint.auth.sessions import DB, CipherDep, CurrentUser, SettingsDep, csrf_protect
from waypoint.discovery.models import JobPosting
from waypoint.errors import ApiError
from waypoint.vault.schemas import Strict

router = APIRouter(prefix="/api/v1/ai", tags=["ai"], dependencies=[Depends(csrf_protect)])

Provider = Literal["none", "anthropic", "openai", "ollama"]
ModelName = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=100, pattern=r"^[\w.:/@-]*$")
]


def _endpoint(value: str) -> str:
    if value and not value.startswith(("https://", "http://")):
        raise ValueError("must start with https:// or http://")
    return value.rstrip("/")


Endpoint = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=500), AfterValidator(_endpoint)
]


class AiSettingsIn(Strict):
    provider: Provider
    base_url: Endpoint = ""
    # None keeps the stored key; "" removes it.
    api_key: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None
    model_scoring: ModelName = ""
    model_writing: ModelName = ""
    model_chat: ModelName = ""
    redact_pii: bool = True
    auto_score: bool = True
    monthly_budget_cents: Annotated[int, Field(ge=0, le=100_000_00)] | None = None


class AiSettingsOut(BaseModel):
    provider: str
    base_url: str
    has_api_key: bool
    model_scoring: str
    model_writing: str
    model_chat: str
    redact_pii: bool
    auto_score: bool
    monthly_budget_cents: int | None
    defaults: dict[str, dict[str, str]]
    default_base_urls: dict[str, str]
    private_endpoints_allowed: bool
    month_spend_micros: int


class TestOut(BaseModel):
    ok: bool
    model: str
    duration_ms: int


class FeatureUsage(BaseModel):
    feature: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_micros: int


class RunOut(BaseModel):
    feature: str
    model: str
    status: str
    error: str
    input_tokens: int
    output_tokens: int
    cost_micros: int | None
    duration_ms: int
    created_at: datetime


class UsageOut(BaseModel):
    month_cost_micros: int
    month_calls: int
    unknown_cost_calls: int
    by_feature: list[FeatureUsage]
    recent: list[RunOut]


class ScoreIn(Strict):
    method: Literal["ai", "rules"] = "ai"


class _Ok(BaseModel):
    ok: bool


async def _out(
    db: DB, request: Request, user_id: uuid.UUID, row: AiSettings | None
) -> AiSettingsOut:
    settings = request.app.state.settings
    allowed = await allow_private_for(db, user_id, settings.outbound_allow_private)
    spend = await month_spend_micros(db, user_id)
    base = {
        "defaults": DEFAULT_MODELS,
        "default_base_urls": DEFAULT_BASE_URL,
        "private_endpoints_allowed": allowed,
        "month_spend_micros": spend,
    }
    if row is None:
        return AiSettingsOut(
            provider="none",
            base_url="",
            has_api_key=False,
            model_scoring="",
            model_writing="",
            model_chat="",
            redact_pii=True,
            auto_score=True,
            monthly_budget_cents=None,
            **base,
        )
    return AiSettingsOut(
        provider=row.provider,
        base_url=row.base_url,
        has_api_key=bool(row.api_key_enc),
        model_scoring=row.model_scoring,
        model_writing=row.model_writing,
        model_chat=row.model_chat,
        redact_pii=row.redact_pii,
        auto_score=row.auto_score,
        monthly_budget_cents=row.monthly_budget_cents,
        **base,
    )


@router.get("/settings")
async def get_ai_settings(request: Request, auth: CurrentUser, db: DB) -> AiSettingsOut:
    return await _out(db, request, auth.user.id, await db.get(AiSettings, auth.user.id))


@router.put("/settings")
async def put_ai_settings(
    body: AiSettingsIn, request: Request, auth: CurrentUser, db: DB, cipher: CipherDep
) -> AiSettingsOut:
    uid = auth.user.id
    if request.app.state.settings.demo_mode and body.provider != "none":
        raise ApiError(403, "demo_mode", "AI providers are turned off in demo mode.")
    row = await db.get(AiSettings, uid)
    if row is None:
        row = AiSettings(user_id=uid)
        db.add(row)
    previous = row.provider
    for key in (
        "provider",
        "base_url",
        "model_scoring",
        "model_writing",
        "model_chat",
        "redact_pii",
        "auto_score",
        "monthly_budget_cents",
    ):
        setattr(row, key, getattr(body, key))
    if body.api_key is not None:
        row.api_key_enc = encrypt_key(cipher, uid, body.api_key)
    if body.provider == "openai" and not (
        body.model_scoring and body.model_writing and body.model_chat
    ):
        raise ApiError(
            422, "models_required", "Name a model for each task for an OpenAI-compatible endpoint."
        )
    if body.provider == "ollama" and not (
        body.model_scoring and body.model_writing and body.model_chat
    ):
        raise ApiError(422, "models_required", "Name an installed Ollama model for each task.")
    if body.provider == "anthropic" and not row.api_key_enc:
        raise ApiError(422, "key_required", "Add your Anthropic API key.")
    audit.record(
        db,
        request,
        "ai.settings_updated",
        uid,
        provider=body.provider,
        previous=previous,
        key_changed=body.api_key is not None,
    )
    await db.commit()
    await db.refresh(row)
    return await _out(db, request, uid, row)


@router.post("/settings/test")
async def test_connection(
    request: Request, auth: CurrentUser, db: DB, cipher: CipherDep, settings: SettingsDep
) -> TestOut:
    uid = auth.user.id
    started = time.monotonic()
    try:
        ai = await client_for(
            db,
            uid,
            cipher,
            allow_private=await allow_private_for(db, uid, settings.outbound_allow_private),
        )
        await structured(
            ai,
            feature="test",
            prompt_name="connection_test",
            values={},
            schema_model=_Ok,
            max_tokens=200,
            effort="low",
        )
    except AiError as exc:
        raise to_api_error(exc) from None
    return TestOut(
        ok=True,
        model=model_for(ai.settings, "test"),
        duration_ms=int((time.monotonic() - started) * 1000),
    )


@router.get("/usage")
async def usage(auth: CurrentUser, db: DB) -> UsageOut:
    uid = auth.user.id
    start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    rows = (
        await db.execute(
            select(
                AiRun.feature,
                func.count(),
                func.coalesce(func.sum(AiRun.input_tokens), 0),
                func.coalesce(func.sum(AiRun.output_tokens), 0),
                func.coalesce(func.sum(AiRun.cost_micros), 0),
                func.count().filter(AiRun.cost_micros.is_(None)),
            )
            .where(AiRun.user_id == uid, AiRun.created_at >= start)
            .group_by(AiRun.feature)
            .order_by(AiRun.feature)
        )
    ).all()
    recent = await db.scalars(
        select(AiRun).where(AiRun.user_id == uid).order_by(AiRun.created_at.desc()).limit(25)
    )
    return UsageOut(
        month_cost_micros=sum(int(r[4] or 0) for r in rows),
        month_calls=sum(int(r[1] or 0) for r in rows),
        unknown_cost_calls=sum(int(r[5] or 0) for r in rows),
        by_feature=[
            FeatureUsage(
                feature=r[0], calls=r[1], input_tokens=r[2], output_tokens=r[3], cost_micros=r[4]
            )
            for r in rows
        ],
        recent=[RunOut.model_validate(r, from_attributes=True) for r in recent],
    )


@router.post("/score/{posting_id}")
async def score_now(
    posting_id: uuid.UUID,
    body: ScoreIn,
    request: Request,
    auth: CurrentUser,
    db: DB,
    cipher: CipherDep,
    settings: SettingsDep,
) -> dict[str, object]:
    uid = auth.user.id
    posting = await db.scalar(
        select(JobPosting).where(JobPosting.user_id == uid, JobPosting.id == posting_id)
    )
    if posting is None:
        raise ApiError(404, "not_found", "Not found.")
    try:
        ai = None
        if body.method == "ai":
            ai = await client_for(
                db,
                uid,
                cipher,
                allow_private=await allow_private_for(db, uid, settings.outbound_allow_private),
            )
        await score_posting(db, uid, posting, ai=ai)
    except AiError as exc:
        raise to_api_error(exc) from None
    await db.commit()
    detail = await score_detail(db, uid, posting.id)
    return detail or {}
