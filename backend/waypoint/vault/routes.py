# SPDX-License-Identifier: Apache-2.0
"""Vault API: profile, entries, verification, and stories."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import delete, func, select, update

from waypoint import audit
from waypoint.ai.jobs import queue_rescore
from waypoint.auth.sessions import DB, CipherDep, CurrentUser, csrf_protect, utcnow
from waypoint.errors import ApiError
from waypoint.models import ENTRY_KINDS, Story, VaultEntry, VaultProfile
from waypoint.security import contexts
from waypoint.vault import service
from waypoint.vault.schemas import (
    CountOut,
    EntryIn,
    EntryOut,
    EntryPatch,
    Kind,
    KindCount,
    ProfileIn,
    ProfileOut,
    StoryIn,
    StoryOut,
    StoryPatch,
    SummaryOut,
    VerifyIn,
    parse_data,
    sort_date,
)

router = APIRouter(prefix="/api/v1/vault", tags=["vault"], dependencies=[Depends(csrf_protect)])

_MAX_ENTRIES_LISTED = 5000
_CONTACT_FIELDS = ("email", "phone", "address")


# --- profile -----------------------------------------------------------------------------


def _profile_context(user_id: uuid.UUID, field: str) -> str:
    return contexts.profile(user_id, field)


@router.get("/profile")
async def get_profile(auth: CurrentUser, db: DB, cipher: CipherDep) -> ProfileOut:
    row = await db.get(VaultProfile, auth.user.id)
    if row is None:
        return ProfileOut()
    contact = {
        f: cipher.decrypt(enc, _profile_context(row.user_id, f))
        for f in _CONTACT_FIELDS
        if (enc := getattr(row, f"{f}_enc"))
    }
    return ProfileOut(
        full_name=row.full_name,
        headline=row.headline,
        location_text=row.location_text,
        summary=row.summary,
        **contact,
    )


@router.put("/profile")
async def put_profile(
    body: ProfileIn, request: Request, auth: CurrentUser, db: DB, cipher: CipherDep
) -> ProfileOut:
    uid = auth.user.id
    row = await db.get(VaultProfile, uid)
    if row is None:
        row = VaultProfile(user_id=uid)
        db.add(row)
    row.full_name = body.full_name
    row.headline = body.headline
    row.location_text = body.location_text
    row.summary = body.summary
    for f in _CONTACT_FIELDS:
        value: str = getattr(body, f)
        setattr(row, f"{f}_enc", cipher.encrypt(value, _profile_context(uid, f)) if value else None)
    audit.record(db, request, "vault.profile_updated", uid)
    await db.commit()
    return ProfileOut(**body.model_dump())


# --- entries -----------------------------------------------------------------------------


@router.get("/entries")
async def list_entries(
    auth: CurrentUser,
    db: DB,
    kind: Kind | None = None,
    verified: bool | None = None,
    import_id: uuid.UUID | None = None,
    q: str | None = Query(default=None, max_length=200),
) -> list[EntryOut]:
    uid = auth.user.id
    stmt = (
        select(VaultEntry)
        .where(VaultEntry.user_id == uid)
        .order_by(VaultEntry.sort_date.desc().nulls_last(), VaultEntry.created_at)
        .limit(_MAX_ENTRIES_LISTED)
    )
    if kind is not None:
        stmt = stmt.where(VaultEntry.kind == kind)
    if verified is not None:
        stmt = stmt.where(VaultEntry.verified == verified)
    if import_id is not None:
        stmt = stmt.where(VaultEntry.import_id == import_id)
    entries = list(await db.scalars(stmt))
    if q and q.strip():
        entries = [e for e in entries if service.matches(e, q.strip())]
    index = await service.verification_index(db, uid)
    return [service.entry_out(e, index) for e in entries]


@router.post("/entries", status_code=201)
async def create_entry(body: EntryIn, auth: CurrentUser, db: DB) -> EntryOut:
    uid = auth.user.id
    data = parse_data(body.kind, body.data)
    await service.check_parent(db, uid, body.kind, body.parent_id)
    entry = VaultEntry(
        user_id=uid,
        kind=body.kind,
        parent_id=body.parent_id,
        data=data,
        evidence_note=body.evidence_note,
        verified=body.verified,
        verified_at=service.stamp(body.verified, utcnow()),
        source="manual",
        sort_date=sort_date(body.kind, data),
    )
    db.add(entry)
    await queue_rescore(db, auth.user.id)
    await db.commit()
    await db.refresh(entry)
    return service.entry_out(entry, await service.verification_index(db, uid))


@router.get("/entries/{entry_id}")
async def get_entry(entry_id: uuid.UUID, auth: CurrentUser, db: DB) -> EntryOut:
    entry = await service.get_entry(db, auth.user.id, entry_id)
    return service.entry_out(entry, await service.verification_index(db, auth.user.id))


@router.patch("/entries/{entry_id}")
async def patch_entry(entry_id: uuid.UUID, body: EntryPatch, auth: CurrentUser, db: DB) -> EntryOut:
    uid = auth.user.id
    entry = await service.get_entry(db, uid, entry_id)
    fields = body.model_fields_set
    now = utcnow()

    kind = body.kind or entry.kind
    kind_changed = kind != entry.kind
    if kind_changed:
        if body.data is None:
            raise ApiError(422, "data_required", "Send the entry's fields when changing its kind.")
        if await service.has_children(db, uid, entry.id):
            raise ApiError(409, "has_children", "Move or delete the items under this entry first.")

    content_changed = False
    if body.data is not None or kind_changed:
        data = parse_data(kind, body.data if body.data is not None else entry.data)
        content_changed = data != entry.data or kind_changed
        entry.data = data
        entry.kind = kind
        entry.sort_date = sort_date(kind, data)

    parent_id = body.parent_id if "parent_id" in fields else entry.parent_id
    if kind_changed or parent_id != entry.parent_id:
        await service.check_parent(db, uid, kind, parent_id, self_id=entry.id)
        entry.parent_id = parent_id

    if body.evidence_note is not None:
        entry.evidence_note = body.evidence_note

    if body.verified is not None and body.verified != entry.verified:
        entry.verified = body.verified
        entry.verified_at = service.stamp(body.verified, now)
    elif entry.verified and content_changed:
        entry.verified_at = now

    await queue_rescore(db, auth.user.id)
    await db.commit()
    await db.refresh(entry)
    return service.entry_out(entry, await service.verification_index(db, uid))


@router.delete("/entries/{entry_id}", status_code=204)
async def delete_entry(entry_id: uuid.UUID, auth: CurrentUser, db: DB) -> Response:
    result = await db.execute(
        delete(VaultEntry).where(VaultEntry.user_id == auth.user.id, VaultEntry.id == entry_id)
    )
    if result.rowcount == 0:  # type: ignore[attr-defined]
        raise service.not_found()
    await queue_rescore(db, auth.user.id)
    await db.commit()
    return Response(status_code=204)


@router.post("/entries/verify")
async def verify_entries(body: VerifyIn, request: Request, auth: CurrentUser, db: DB) -> CountOut:
    uid = auth.user.id
    result = await db.execute(
        update(VaultEntry)
        .where(
            VaultEntry.user_id == uid,
            VaultEntry.id.in_(body.ids),
            VaultEntry.verified.is_not(body.verified),
        )
        .values(verified=body.verified, verified_at=service.stamp(body.verified, utcnow()))
    )
    count: int = result.rowcount  # type: ignore[attr-defined]
    audit.record(
        db,
        request,
        "vault.verify",
        uid,
        verified=body.verified,
        requested=len(body.ids),
        count=count,
    )
    await queue_rescore(db, auth.user.id)
    await db.commit()
    return CountOut(updated=count)


@router.get("/summary")
async def summary(auth: CurrentUser, db: DB) -> SummaryOut:
    uid = auth.user.id
    kinds = {k: KindCount(total=0, verified=0) for k in ENTRY_KINDS}
    rows = await db.execute(
        select(VaultEntry.kind, func.count(), func.count().filter(VaultEntry.verified.is_(True)))
        .where(VaultEntry.user_id == uid)
        .group_by(VaultEntry.kind)
    )
    for kind, total, done in rows:
        kinds[kind] = KindCount(total=total, verified=done)
    st_total, st_done = (
        await db.execute(
            select(func.count(), func.count().filter(Story.verified.is_(True))).where(
                Story.user_id == uid
            )
        )
    ).one()
    stories = KindCount(total=st_total, verified=st_done)
    unverified = sum(k.total - k.verified for k in kinds.values()) + (st_total - st_done)
    return SummaryOut(kinds=kinds, stories=stories, needs_review=unverified)


# --- stories -----------------------------------------------------------------------------


@router.get("/stories")
async def list_stories(
    auth: CurrentUser, db: DB, q: str | None = Query(default=None, max_length=200)
) -> list[StoryOut]:
    uid = auth.user.id
    stories = list(
        await db.scalars(
            select(Story).where(Story.user_id == uid).order_by(Story.updated_at.desc())
        )
    )
    if q and q.strip():
        needle = q.strip().casefold()
        stories = [
            s
            for s in stories
            if any(
                needle in v.casefold()
                for v in (s.title, s.situation, s.task, s.action, s.result, s.reflection, *s.tags)
            )
        ]
    links = await service.story_links(db, uid, (s.id for s in stories))
    return [service.story_out(s, links[s.id]) for s in stories]


@router.post("/stories", status_code=201)
async def create_story(body: StoryIn, auth: CurrentUser, db: DB) -> StoryOut:
    uid = auth.user.id
    story = Story(
        user_id=uid,
        **body.model_dump(exclude={"entry_ids", "verified"}),
        verified=body.verified,
        verified_at=service.stamp(body.verified, utcnow()),
    )
    db.add(story)
    await db.flush()
    await service.set_story_links(db, uid, story.id, body.entry_ids)
    await db.commit()
    await db.refresh(story)
    links = await service.story_links(db, uid, [story.id])
    return service.story_out(story, links[story.id])


@router.get("/stories/{story_id}")
async def get_story(story_id: uuid.UUID, auth: CurrentUser, db: DB) -> StoryOut:
    story = await service.get_story(db, auth.user.id, story_id)
    links = await service.story_links(db, auth.user.id, [story.id])
    return service.story_out(story, links[story.id])


@router.patch("/stories/{story_id}")
async def patch_story(story_id: uuid.UUID, body: StoryPatch, auth: CurrentUser, db: DB) -> StoryOut:
    uid = auth.user.id
    story = await service.get_story(db, uid, story_id)
    changes = body.model_dump(exclude_none=True, exclude={"entry_ids", "verified"})
    for key, value in changes.items():
        setattr(story, key, value)
    now = utcnow()
    if body.verified is not None and body.verified != story.verified:
        story.verified = body.verified
        story.verified_at = service.stamp(body.verified, now)
    elif story.verified and changes:
        story.verified_at = now
    if body.entry_ids is not None:
        await service.set_story_links(db, uid, story.id, body.entry_ids)
    await db.commit()
    await db.refresh(story)
    links = await service.story_links(db, uid, [story.id])
    return service.story_out(story, links[story.id])


@router.delete("/stories/{story_id}", status_code=204)
async def delete_story(story_id: uuid.UUID, auth: CurrentUser, db: DB) -> Response:
    result = await db.execute(
        delete(Story).where(Story.user_id == auth.user.id, Story.id == story_id)
    )
    if result.rowcount == 0:  # type: ignore[attr-defined]
        raise service.not_found()
    await db.commit()
    return Response(status_code=204)
