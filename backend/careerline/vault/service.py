# SPDX-License-Identifier: Apache-2.0
"""Per-user data access for the vault.

Every query in this module filters by ``user_id``. Routes must go through these
helpers (or add the same filter) so one user can never read or change another
user's rows. A row that belongs to someone else is reported as "not found".
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from careerline.errors import ApiError
from careerline.models import ResumeImport, Story, StoryEntry, VaultEntry
from careerline.vault.schemas import ALLOWED_PARENTS, EntryOut, StoryOut

_KIND_LABEL = {
    "role": "role",
    "project": "project",
    "education": "education entry",
}


def not_found(message: str = "Not found.") -> ApiError:
    return ApiError(404, "not_found", message)


async def get_entry(db: AsyncSession, user_id: uuid.UUID, entry_id: uuid.UUID) -> VaultEntry:
    entry = await db.scalar(
        select(VaultEntry).where(VaultEntry.user_id == user_id, VaultEntry.id == entry_id)
    )
    if entry is None:
        raise not_found()
    return entry


async def get_story(db: AsyncSession, user_id: uuid.UUID, story_id: uuid.UUID) -> Story:
    story = await db.scalar(select(Story).where(Story.user_id == user_id, Story.id == story_id))
    if story is None:
        raise not_found()
    return story


async def get_import(db: AsyncSession, user_id: uuid.UUID, import_id: uuid.UUID) -> ResumeImport:
    row = await db.scalar(
        select(ResumeImport).where(ResumeImport.user_id == user_id, ResumeImport.id == import_id)
    )
    if row is None:
        raise not_found()
    return row


async def check_parent(
    db: AsyncSession,
    user_id: uuid.UUID,
    kind: str,
    parent_id: uuid.UUID | None,
    self_id: uuid.UUID | None = None,
) -> None:
    allowed = ALLOWED_PARENTS[kind]
    if parent_id is None:
        if None not in allowed:
            names = " or ".join(sorted(_KIND_LABEL.get(k, k) for k in allowed if k))
            raise ApiError(422, "parent_required", f"Choose the {names} this belongs to.")
        return
    if parent_id == self_id:
        raise ApiError(422, "invalid_parent", "An entry can't belong to itself.")
    parent = await db.scalar(
        select(VaultEntry.kind).where(VaultEntry.user_id == user_id, VaultEntry.id == parent_id)
    )
    if parent is None:
        raise not_found("The parent entry was not found.")
    if parent not in allowed:
        raise ApiError(422, "invalid_parent", f"A {kind} can't belong to a {parent}.")


async def has_children(db: AsyncSession, user_id: uuid.UUID, entry_id: uuid.UUID) -> bool:
    child = await db.scalar(
        select(VaultEntry.id)
        .where(VaultEntry.user_id == user_id, VaultEntry.parent_id == entry_id)
        .limit(1)
    )
    return child is not None


async def verification_index(
    db: AsyncSession, user_id: uuid.UUID
) -> dict[uuid.UUID, tuple[uuid.UUID | None, bool]]:
    """Map entry id -> (parent id, verified) for all of the user's entries."""
    rows = await db.execute(
        select(VaultEntry.id, VaultEntry.parent_id, VaultEntry.verified).where(
            VaultEntry.user_id == user_id
        )
    )
    return {r.id: (r.parent_id, r.verified) for r in rows}


def is_usable(index: dict[uuid.UUID, tuple[uuid.UUID | None, bool]], entry_id: uuid.UUID) -> bool:
    """An entry is usable by the AI only if it and every ancestor are verified."""
    current: uuid.UUID | None = entry_id
    for _ in range(8):  # parent chains are at most three deep
        if current is None:
            return True
        node = index.get(current)
        if node is None or not node[1]:
            return False
        current = node[0]
    return False


def entry_out(entry: VaultEntry, index: dict[uuid.UUID, tuple[uuid.UUID | None, bool]]) -> EntryOut:
    return EntryOut(
        id=entry.id,
        kind=entry.kind,
        parent_id=entry.parent_id,
        data=entry.data,
        evidence_note=entry.evidence_note,
        verified=entry.verified,
        verified_at=entry.verified_at,
        usable=is_usable(index, entry.id),
        source=entry.source,
        import_id=entry.import_id,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


async def story_links(
    db: AsyncSession, user_id: uuid.UUID, story_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, list[uuid.UUID]]:
    ids = list(story_ids)
    links: dict[uuid.UUID, list[uuid.UUID]] = {i: [] for i in ids}
    if not ids:
        return links
    rows = await db.execute(
        select(StoryEntry.story_id, StoryEntry.entry_id).where(
            StoryEntry.user_id == user_id, StoryEntry.story_id.in_(ids)
        )
    )
    for r in rows:
        links[r.story_id].append(r.entry_id)
    return links


async def set_story_links(
    db: AsyncSession, user_id: uuid.UUID, story_id: uuid.UUID, entry_ids: list[uuid.UUID]
) -> None:
    unique = list(dict.fromkeys(entry_ids))
    if unique:
        found = await db.scalars(
            select(VaultEntry.id).where(VaultEntry.user_id == user_id, VaultEntry.id.in_(unique))
        )
        if len(set(found)) != len(unique):
            raise not_found("One or more linked entries were not found.")
    existing = await db.scalars(
        select(StoryEntry).where(StoryEntry.user_id == user_id, StoryEntry.story_id == story_id)
    )
    for link in existing:
        await db.delete(link)
    await db.flush()
    db.add_all(StoryEntry(user_id=user_id, story_id=story_id, entry_id=e) for e in unique)


def story_out(story: Story, entry_ids: list[uuid.UUID]) -> StoryOut:
    return StoryOut(
        id=story.id,
        title=story.title,
        situation=story.situation,
        task=story.task,
        action=story.action,
        result=story.result,
        reflection=story.reflection,
        tags=list(story.tags),
        entry_ids=entry_ids,
        verified=story.verified,
        verified_at=story.verified_at,
        created_at=story.created_at,
        updated_at=story.updated_at,
    )


def matches(entry: VaultEntry, needle: str) -> bool:
    needle = needle.casefold()
    values = [str(v) for v in entry.data.values() if isinstance(v, str | int)]
    metric = entry.data.get("metric")
    if isinstance(metric, dict):
        values.extend(str(v) for v in metric.values())
    values.append(entry.evidence_note)
    return any(needle in v.casefold() for v in values)


def stamp(verified: bool, now: datetime) -> datetime | None:
    return now if verified else None
