# SPDX-License-Identifier: Apache-2.0
"""Discovery API: sources, postings, filters, and one-off imports."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from careerline import audit
from careerline.ai.scoring import score_detail, scores_for
from careerline.auth.sessions import DB, CipherDep, CurrentUser, csrf_protect, utcnow
from careerline.discovery.adapters import ADAPTERS
from careerline.discovery.models import (
    JobFilters,
    JobPosting,
    JobSource,
    PostingSighting,
    QueuedJob,
)
from careerline.discovery.normalize import fingerprint
from careerline.discovery.page_import import ImportRefused, from_text, import_url
from careerline.discovery.schemas import (
    AdapterOut,
    FilterOut,
    FiltersIn,
    FiltersOut,
    ImportIn,
    PostingDetail,
    PostingList,
    PostingPatch,
    PostingSummary,
    SightingOut,
    SourceIn,
    SourceOut,
    SourcePatch,
)
from careerline.discovery.service import evaluate, store_postings, track
from careerline.errors import ApiError
from careerline.net import SafeFetcher
from careerline.security import contexts
from careerline.security.crypto import FieldCipher
from careerline.security.ratelimit import check_and_hit
from careerline.worker.queue import enqueue

router = APIRouter(
    prefix="/api/v1/discovery", tags=["discovery"], dependencies=[Depends(csrf_protect)]
)

MAX_LISTED = 1000
IMPORTS_PER_HOUR = 30


def fetcher_for(request: Request) -> SafeFetcher:
    factory = request.app.state.fetcher_factory
    fetcher: SafeFetcher = factory()
    return fetcher


def _secret_context(user_id: uuid.UUID, source_id: uuid.UUID) -> str:
    return contexts.source_secret(user_id, source_id)


def decrypt_secret(cipher: FieldCipher, source: JobSource) -> dict[str, Any]:
    if not source.secret_enc:
        return {}
    data: dict[str, Any] = json.loads(
        cipher.decrypt(source.secret_enc, _secret_context(source.user_id, source.id))
    )
    return data


def _validate(model: type[BaseModel], raw: dict[str, Any], where: str) -> dict[str, Any]:
    try:
        return model.model_validate(raw).model_dump(mode="json")
    except ValidationError as exc:
        raise RequestValidationError(
            [
                {"loc": ("body", where, *e["loc"]), "msg": e["msg"], "type": e["type"]}
                for e in exc.errors()
            ]
        ) from None


async def _get_source(db: AsyncSession, uid: uuid.UUID, source_id: uuid.UUID) -> JobSource:
    source = await db.scalar(
        select(JobSource).where(JobSource.user_id == uid, JobSource.id == source_id)
    )
    if source is None:
        raise ApiError(404, "not_found", "Not found.")
    return source


async def _get_posting(db: AsyncSession, uid: uuid.UUID, posting_id: uuid.UUID) -> JobPosting:
    posting = await db.scalar(
        select(JobPosting).where(JobPosting.user_id == uid, JobPosting.id == posting_id)
    )
    if posting is None:
        raise ApiError(404, "not_found", "Not found.")
    return posting


async def _pending_sources(db: AsyncSession, uid: uuid.UUID) -> set[str]:
    rows = await db.scalars(
        select(QueuedJob.dedupe_key).where(
            QueuedJob.user_id == uid,
            QueuedJob.kind == "fetch_source",
            QueuedJob.status.in_(("queued", "running")),
        )
    )
    return {k for k in rows if k}


def _source_out(s: JobSource, pending: set[str]) -> SourceOut:
    return SourceOut(
        id=s.id,
        kind=s.kind,
        label=ADAPTERS[s.kind].label,
        name=s.name,
        enabled=s.enabled,
        interval_hours=s.interval_hours,
        config=s.config,
        has_secret=bool(s.secret_enc),
        last_run_at=s.last_run_at,
        next_run_at=s.next_run_at,
        last_status=s.last_status,
        last_error=s.last_error,
        last_found=s.last_found,
        last_new=s.last_new,
        pending=f"fetch:{s.id}" in pending,
        created_at=s.created_at,
    )


async def queue_fetch(db: AsyncSession, source: JobSource) -> bool:
    return await enqueue(
        db,
        "fetch_source",
        {"source_id": str(source.id)},
        user_id=source.user_id,
        dedupe_key=f"fetch:{source.id}",
        max_attempts=2,
    )


# --- sources -------------------------------------------------------------------------------


@router.get("/adapters")
async def list_adapters(_: CurrentUser) -> list[AdapterOut]:
    return [
        AdapterOut(
            kind=a.kind,
            label=a.label,
            description=a.description,
            terms_url=a.terms_url,
            config_fields=a.config_fields,
            secret_fields=a.secret_fields,
        )
        for a in ADAPTERS.values()
    ]


@router.get("/sources")
async def list_sources(auth: CurrentUser, db: DB) -> list[SourceOut]:
    uid = auth.user.id
    pending = await _pending_sources(db, uid)
    rows = await db.scalars(
        select(JobSource).where(JobSource.user_id == uid).order_by(JobSource.created_at)
    )
    return [_source_out(s, pending) for s in rows]


@router.post("/sources", status_code=201)
async def create_source(
    body: SourceIn, request: Request, auth: CurrentUser, db: DB, cipher: CipherDep
) -> SourceOut:
    uid = auth.user.id
    info = ADAPTERS[body.kind]
    config = _validate(info.config_model, body.config, "config")
    secret = _validate(info.secret_model, body.secret or {}, "secret")
    source = JobSource(
        id=uuid.uuid4(),
        user_id=uid,
        kind=body.kind,
        name=body.name,
        enabled=body.enabled,
        interval_hours=body.interval_hours,
        config=config,
        next_run_at=utcnow() if body.enabled else None,
    )
    if secret:
        source.secret_enc = cipher.encrypt(json.dumps(secret), _secret_context(uid, source.id))
    db.add(source)
    await db.flush()
    pending: set[str] = set()
    if body.enabled and await queue_fetch(db, source):
        pending.add(f"fetch:{source.id}")
    audit.record(
        db, request, "discovery.source_created", uid, source_id=str(source.id), kind=body.kind
    )
    await db.commit()
    await db.refresh(source)
    return _source_out(source, pending)


@router.patch("/sources/{source_id}")
async def patch_source(
    source_id: uuid.UUID, body: SourcePatch, auth: CurrentUser, db: DB, cipher: CipherDep
) -> SourceOut:
    uid = auth.user.id
    source = await _get_source(db, uid, source_id)
    info = ADAPTERS[source.kind]
    if body.name is not None:
        source.name = body.name
    if body.interval_hours is not None:
        source.interval_hours = body.interval_hours
    if body.config is not None:
        source.config = _validate(info.config_model, body.config, "config")
    if body.secret is not None:
        secret = _validate(info.secret_model, body.secret, "secret")
        source.secret_enc = (
            cipher.encrypt(json.dumps(secret), _secret_context(uid, source.id)) if secret else None
        )
    if body.enabled is not None and body.enabled != source.enabled:
        source.enabled = body.enabled
        source.next_run_at = utcnow() if body.enabled else None
    await db.commit()
    await db.refresh(source)
    return _source_out(source, await _pending_sources(db, uid))


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(
    source_id: uuid.UUID, request: Request, auth: CurrentUser, db: DB
) -> Response:
    uid = auth.user.id
    source = await _get_source(db, uid, source_id)
    await db.execute(
        delete(QueuedJob).where(
            QueuedJob.dedupe_key == f"fetch:{source.id}", QueuedJob.status == "queued"
        )
    )
    # Sightings cascade. Postings stay (they may be saved or tracked).
    await db.delete(source)
    audit.record(db, request, "discovery.source_deleted", uid, source_id=str(source.id))
    await db.commit()
    return Response(status_code=204)


@router.post("/sources/{source_id}/run", status_code=202)
async def run_source(
    source_id: uuid.UUID, request: Request, auth: CurrentUser, db: DB
) -> SourceOut:
    uid = auth.user.id
    source = await _get_source(db, uid, source_id)
    if request.app.state.settings.demo_mode:
        raise ApiError(403, "demo_mode", "Job sources can't be fetched in demo mode.")
    if not source.enabled:
        raise ApiError(409, "source_disabled", "Turn the source on before fetching.")
    await check_and_hit(db, f"source_run:{uid}", 60, timedelta(hours=1))
    await queue_fetch(db, source)
    await db.commit()
    return _source_out(source, await _pending_sources(db, uid))


# --- postings ------------------------------------------------------------------------------


async def _sources_by_posting(
    db: AsyncSession, uid: uuid.UUID, ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[PostingSighting]]:
    out: dict[uuid.UUID, list[PostingSighting]] = {i: [] for i in ids}
    if not ids:
        return out
    for s in await db.scalars(
        select(PostingSighting)
        .where(PostingSighting.user_id == uid, PostingSighting.posting_id.in_(ids))
        .order_by(PostingSighting.seen_at)
    ):
        out[s.posting_id].append(s)
    return out


def _summary(
    p: JobPosting, sightings: list[PostingSighting], filters: JobFilters | None
) -> dict[str, Any]:
    r = evaluate(p, filters)
    return {
        "id": p.id,
        "title": p.title,
        "company": p.company,
        "location": p.location,
        "work_mode": p.work_mode,
        "pay_min_cents": p.pay_min_cents,
        "pay_max_cents": p.pay_max_cents,
        "pay_period": p.pay_period,
        "currency": p.currency,
        "shifts": list(p.shifts),
        "clearance": p.clearance,
        "employment_type": p.employment_type,
        "url": p.url,
        "posted_at": p.posted_at,
        "closes_on": p.closes_on,
        "first_seen_at": p.first_seen_at,
        "last_seen_at": p.last_seen_at,
        "status": p.status,
        "application_id": p.application_id,
        "sources": list(dict.fromkeys(s.source_label for s in sightings if s.source_label)),
        "filter": FilterOut(
            passed=r.passed,
            reasons=r.reasons,
            preferred=r.preferred,
            pay_vs_target=r.pay_vs_target,
            distance_miles=r.distance_miles,
        ),
    }


def _view_of(p: JobPosting, passed: bool) -> str:
    if p.status in ("saved", "tracked"):
        return "saved"
    if p.status == "dismissed":
        return "dismissed"
    return "matches" if passed else "filtered"


@router.get("/postings")
async def list_postings(
    request: Request,
    auth: CurrentUser,
    db: DB,
    view: str = Query(default="matches", pattern="^(matches|filtered|saved|dismissed|all)$"),
    q: str | None = Query(default=None, max_length=200),
) -> PostingList:
    uid = auth.user.id
    filters = await db.get(JobFilters, uid)
    postings = list(
        await db.scalars(
            select(JobPosting)
            .where(JobPosting.user_id == uid)
            .order_by(JobPosting.first_seen_at.desc())
            .limit(5000)
        )
    )
    sightings = await _sources_by_posting(db, uid, [p.id for p in postings])
    scores = await scores_for(db, uid, [p.id for p in postings])
    counts = {"matches": 0, "filtered": 0, "saved": 0, "dismissed": 0, "all": len(postings)}
    items: list[PostingSummary] = []
    needle = (q or "").strip().casefold()
    for p in postings:
        data = _summary(p, sightings[p.id], filters)
        v = _view_of(p, data["filter"].passed)
        counts[v] += 1
        if view not in ("all", v):
            continue
        if needle and not any(needle in s.casefold() for s in (p.title, p.company, p.location)):
            continue
        items.append(PostingSummary(**data, score=scores.get(p.id)))
    if view == "matches":
        items.sort(
            key=lambda i: (
                not i.filter.preferred,
                -(i.score.score if i.score else -1),
                -(i.first_seen_at.timestamp()),
            )
        )
    return PostingList(items=items[:MAX_LISTED], counts=counts)


async def posting_detail(
    request: Request, db: AsyncSession, uid: uuid.UUID, p: JobPosting
) -> PostingDetail:
    filters = await db.get(JobFilters, uid)
    sightings = (await _sources_by_posting(db, uid, [p.id]))[p.id]
    scores = await scores_for(db, uid, [p.id])
    return PostingDetail(
        **_summary(p, sightings, filters),
        score=scores.get(p.id),
        description=p.description,
        sightings=[
            SightingOut(source_label=s.source_label, url=s.url, seen_at=s.seen_at)
            for s in sightings
        ],
        score_detail=await score_detail(db, uid, p.id),
    )


@router.get("/postings/{posting_id}")
async def get_posting(
    posting_id: uuid.UUID, request: Request, auth: CurrentUser, db: DB
) -> PostingDetail:
    posting = await _get_posting(db, auth.user.id, posting_id)
    return await posting_detail(request, db, auth.user.id, posting)


@router.patch("/postings/{posting_id}")
async def patch_posting(
    posting_id: uuid.UUID, body: PostingPatch, request: Request, auth: CurrentUser, db: DB
) -> PostingDetail:
    posting = await _get_posting(db, auth.user.id, posting_id)
    if posting.status == "tracked" and posting.application_id is not None:
        raise ApiError(409, "tracked", "This job is in your tracker. Change it there.")
    posting.status = body.status
    await db.commit()
    await db.refresh(posting)
    return await posting_detail(request, db, auth.user.id, posting)


@router.delete("/postings/{posting_id}", status_code=204)
async def delete_posting(posting_id: uuid.UUID, auth: CurrentUser, db: DB) -> Response:
    posting = await _get_posting(db, auth.user.id, posting_id)
    await db.delete(posting)
    await db.commit()
    return Response(status_code=204)


@router.post("/postings/{posting_id}/track")
async def track_posting(
    posting_id: uuid.UUID, request: Request, auth: CurrentUser, db: DB
) -> PostingDetail:
    """Create a tracker application (stage Saved) from a posting, once."""
    uid = auth.user.id
    p = await _get_posting(db, uid, posting_id)
    if p.application_id is None:
        await track(db, p)
        await db.commit()
        await db.refresh(p)
    return await posting_detail(request, db, uid, p)


@router.post("/import", status_code=201)
async def import_posting(
    body: ImportIn, request: Request, auth: CurrentUser, db: DB
) -> PostingDetail:
    uid = auth.user.id
    posting = await import_one(request, db, uid, body)
    return await posting_detail(request, db, uid, posting)


async def import_one(
    request: Request, db: AsyncSession, uid: uuid.UUID, body: ImportIn
) -> JobPosting:
    """Add one posting from a link or pasted text (also used by the MCP ``import_job`` tool)."""
    if not body.url and not body.text.strip():
        raise ApiError(422, "nothing_to_import", "Paste a link to the posting, or its text.")
    await check_and_hit(db, f"posting_import:{uid}", IMPORTS_PER_HOUR, timedelta(hours=1))
    await db.commit()
    if body.text.strip():
        if not body.title:
            raise ApiError(422, "title_required", "Add the job title for pasted text.")
        normalized = from_text(body.text, body.title, body.company, body.location, body.url)
        label = "Pasted"
    else:
        if request.app.state.settings.demo_mode:
            raise ApiError(
                403, "demo_mode", "Links can't be fetched in demo mode. Paste the description."
            )
        try:
            normalized = await import_url(fetcher_for(request), body.url)
        except ImportRefused as exc:
            raise ApiError(422, exc.code, exc.message) from None
        if body.title:
            normalized.title = body.title
        if body.company:
            normalized.company = body.company
        label = "Imported link"
    result = await store_postings(db, uid, None, [normalized.finish()], source_label=label)
    posting_id = result.new_ids[0] if result.new_ids else None
    if posting_id is None:
        fp = fingerprint(normalized.company, normalized.title, normalized.location)
        posting_id = await db.scalar(
            select(JobPosting.id).where(JobPosting.user_id == uid, JobPosting.fingerprint == fp)
        )
    await enqueue(db, "score_postings", {}, user_id=uid, dedupe_key=f"score:{uid}")
    await db.commit()
    return await _get_posting(db, uid, posting_id)  # type: ignore[arg-type]


# --- filters -------------------------------------------------------------------------------


@router.get("/filters")
async def get_filters(auth: CurrentUser, db: DB) -> FiltersOut:
    row = await db.get(JobFilters, auth.user.id)
    if row is None:
        return FiltersOut()
    return FiltersOut.model_validate(row, from_attributes=True)


@router.put("/filters")
async def put_filters(body: FiltersIn, auth: CurrentUser, db: DB) -> FiltersOut:
    row = await db.get(JobFilters, auth.user.id)
    if row is None:
        row = JobFilters(user_id=auth.user.id)
        db.add(row)
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    await db.commit()
    return FiltersOut(**body.model_dump())
