# SPDX-License-Identifier: Apache-2.0
"""Resume import API.

The upload is the raw request body (``Content-Type: application/pdf`` or the
DOCX type), not multipart. The original file is never stored: only its
extracted text and the profile suggestions are kept, encrypted, until the
import is deleted. Every entry created is an unverified draft.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter
from datetime import timedelta
from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from waypoint import audit
from waypoint.auth.sessions import DB, CipherDep, CurrentUser, csrf_protect, utcnow
from waypoint.errors import ApiError
from waypoint.models import ResumeImport, VaultEntry
from waypoint.security import contexts
from waypoint.security.crypto import FieldCipher
from waypoint.security.ratelimit import check_and_hit
from waypoint.vault import service
from waypoint.vault.importer.extract import extract_text
from waypoint.vault.importer.guards import MIME_FOR, RejectedFile, check_type, clean_filename
from waypoint.vault.importer.parse import parse_resume
from waypoint.vault.schemas import (
    ALLOWED_PARENTS,
    ImportDetailOut,
    ImportOut,
    ProfileSuggestions,
    parse_data,
    sort_date,
)

IMPORT_PATH = "/api/v1/vault/imports"
IMPORTS_PER_HOUR = 20

router = APIRouter(prefix=IMPORT_PATH, tags=["vault"], dependencies=[Depends(csrf_protect)])


def _context(user_id: uuid.UUID, import_id: uuid.UUID, part: str) -> str:
    return contexts.resume_import(user_id, import_id, part)


async def _remaining(db: AsyncSession, user_id: uuid.UUID) -> dict[uuid.UUID, int]:
    rows = await db.execute(
        select(VaultEntry.import_id, func.count())
        .where(
            VaultEntry.user_id == user_id,
            VaultEntry.import_id.is_not(None),
            VaultEntry.verified.is_(False),
        )
        .group_by(VaultEntry.import_id)
    )
    return {r[0]: r[1] for r in rows if r[0] is not None}


def _summary(row: ResumeImport, remaining: int) -> ImportOut:
    return ImportOut(
        id=row.id,
        filename=row.filename,
        mime=row.mime,
        size_bytes=row.size_bytes,
        stats=row.stats,
        drafts_remaining=remaining,
        created_at=row.created_at,
    )


async def _detail(
    db: AsyncSession, cipher: FieldCipher, user_id: uuid.UUID, row: ResumeImport
) -> ImportDetailOut:
    entries = list(
        await db.scalars(
            select(VaultEntry)
            .where(VaultEntry.user_id == user_id, VaultEntry.import_id == row.id)
            .order_by(VaultEntry.created_at, VaultEntry.id)
        )
    )
    index = await service.verification_index(db, user_id)
    suggestions = json.loads(cipher.decrypt(row.suggestions_enc, _context(user_id, row.id, "s")))
    base = _summary(row, sum(1 for e in entries if not e.verified))
    return ImportDetailOut(
        **base.model_dump(),
        text=cipher.decrypt(row.text_enc, _context(user_id, row.id, "text")),
        suggestions=ProfileSuggestions(**suggestions),
        entries=[service.entry_out(e, index) for e in entries],
    )


@router.post("", status_code=201)
async def create_import(
    request: Request,
    auth: CurrentUser,
    db: DB,
    cipher: CipherDep,
    x_filename: str | None = Header(default=None, max_length=1000),
) -> ImportDetailOut:
    uid = auth.user.id
    await check_and_hit(db, f"vault_import:{uid}", IMPORTS_PER_HOUR, timedelta(hours=1))
    await db.commit()  # count the attempt even if the file is rejected

    data = await request.body()
    if not data:
        raise ApiError(422, "empty_file", "The file is empty.")
    try:
        kind = check_type(data, request.headers.get("content-type", ""))
        text = await extract_text(data, kind)
    except RejectedFile as exc:
        raise ApiError(exc.status, exc.code, exc.message) from None

    parsed = parse_resume(text)
    row = ResumeImport(
        id=uuid.uuid4(),
        user_id=uid,
        filename=clean_filename(unquote(x_filename or ""), kind),
        mime=MIME_FOR[kind],
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        stats={},
    )
    allowed_suggestions = ProfileSuggestions.model_fields.keys()
    suggestions = {k: v for k, v in parsed.suggestions.items() if k in allowed_suggestions}
    row.text_enc = cipher.encrypt(text, _context(uid, row.id, "text"))
    row.suggestions_enc = cipher.encrypt(json.dumps(suggestions), _context(uid, row.id, "s"))
    db.add(row)
    await db.flush()

    ids: dict[int, uuid.UUID] = {}
    counts: Counter[str] = Counter()
    skipped = 0
    # One transaction means one now(); spread created_at so drafts keep the resume's order.
    base = utcnow()
    for position, draft in enumerate(parsed.drafts):
        kind_ = draft.kind
        parent_id = ids.get(draft.parent) if draft.parent is not None else None
        if parent_id is None and None not in ALLOWED_PARENTS[kind_]:
            kind_ = "achievement"  # its parent was dropped; keep the text as a loose bullet
        try:
            clean: dict[str, Any] = parse_data(kind_, draft.data)
        except RequestValidationError:
            skipped += 1
            continue
        entry = VaultEntry(
            id=uuid.uuid4(),
            user_id=uid,
            kind=kind_,
            parent_id=parent_id,
            data=clean,
            evidence_note="",
            verified=False,
            source="import",
            import_id=row.id,
            sort_date=sort_date(kind_, clean),
            created_at=base + timedelta(microseconds=position),
            updated_at=base,
        )
        db.add(entry)
        ids[draft.key] = entry.id
        counts[kind_] += 1

    total = sum(counts.values())
    row.stats = {
        "kinds": dict(counts),
        "sections": parsed.sections,
        "skipped": skipped,
        "total": total,
    }
    audit.record(
        db,
        request,
        "vault.import",
        uid,
        import_id=str(row.id),
        mime=row.mime,
        size_bytes=row.size_bytes,
        drafts=total,
    )
    await db.commit()
    await db.refresh(row)
    return await _detail(db, cipher, uid, row)


@router.get("")
async def list_imports(auth: CurrentUser, db: DB) -> list[ImportOut]:
    uid = auth.user.id
    rows = await db.scalars(
        select(ResumeImport)
        .where(ResumeImport.user_id == uid)
        .order_by(ResumeImport.created_at.desc())
    )
    remaining = await _remaining(db, uid)
    return [_summary(r, remaining.get(r.id, 0)) for r in rows]


@router.get("/{import_id}")
async def get_import(
    import_id: uuid.UUID, auth: CurrentUser, db: DB, cipher: CipherDep
) -> ImportDetailOut:
    row = await service.get_import(db, auth.user.id, import_id)
    return await _detail(db, cipher, auth.user.id, row)


@router.delete("/{import_id}", status_code=204)
async def delete_import(
    import_id: uuid.UUID,
    request: Request,
    auth: CurrentUser,
    db: DB,
    discard_drafts: bool = False,
) -> Response:
    uid = auth.user.id
    row = await service.get_import(db, uid, import_id)
    discarded = 0
    if discard_drafts:
        # Keep a draft if something verified hangs under it, so nothing approved is lost.
        child = VaultEntry.__table__.alias("child")
        keep = (
            select(child.c.id)
            .where(
                child.c.user_id == uid,
                child.c.parent_id == VaultEntry.id,
                child.c.verified.is_(True),
            )
            .exists()
        )
        result = await db.execute(
            delete(VaultEntry).where(
                VaultEntry.user_id == uid,
                VaultEntry.import_id == row.id,
                VaultEntry.verified.is_(False),
                ~keep,
            )
        )
        discarded = result.rowcount  # type: ignore[attr-defined]
    await db.delete(row)
    audit.record(
        db, request, "vault.import_deleted", uid, import_id=str(row.id), discarded=discarded
    )
    await db.commit()
    return Response(status_code=204)
