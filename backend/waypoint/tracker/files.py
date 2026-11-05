# SPDX-License-Identifier: Apache-2.0
"""Tracker attachments: encrypted at rest, type-checked by content, download only.

Uploads are the raw request body (like resume import). Files are never rendered
inline: downloads carry ``Content-Disposition: attachment``, the declared type
from the allow-list, ``nosniff``, and a sandboxing CSP.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import timedelta
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from waypoint import audit
from waypoint.auth.sessions import DB, CipherDep, CurrentUser, SettingsDep, csrf_protect, utcnow
from waypoint.config import Settings
from waypoint.errors import ApiError
from waypoint.security.crypto import FieldCipher
from waypoint.security.ratelimit import check_and_hit
from waypoint.tracker import service
from waypoint.tracker.models import Application, ApplicationEvent, Attachment
from waypoint.tracker.schemas import AttachmentOut
from waypoint.vault.importer.guards import (
    DOCX_MIME,
    PDF_MIME,
    RejectedFile,
    check_docx_archive,
    clean_filename,
)

UPLOAD_PATH_PATTERN = r"^/api/v1/tracker/applications/[^/]+/attachments$"
UPLOADS_PER_HOUR = 60

ALLOWED: dict[str, str] = {
    "pdf": PDF_MIME,
    "docx": DOCX_MIME,
    "png": "image/png",
    "jpg": "image/jpeg",
    "txt": "text/plain",
}

router = APIRouter(prefix="/api/v1/tracker", tags=["tracker"], dependencies=[Depends(csrf_protect)])


def sniff_attachment(data: bytes, declared_mime: str) -> str:
    """Return the file extension for an allowed type, or raise ``RejectedFile``."""
    declared = declared_mime.split(";", 1)[0].strip().lower()
    unsupported = RejectedFile(
        415, "unsupported_file", "Attach a PDF, Word (.docx), PNG, JPEG, or plain-text file."
    )
    if data.startswith(b"%PDF-"):
        ext = "pdf"
    elif data.startswith(b"PK\x03\x04"):
        ext = "docx"
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        ext = "png"
    elif data.startswith(b"\xff\xd8\xff"):
        ext = "jpg"
    elif declared == "text/plain":
        if b"\x00" in data:
            raise unsupported
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise unsupported from None
        ext = "txt"
    else:
        raise unsupported
    if ALLOWED[ext] != declared:
        raise unsupported
    if ext == "docx":
        check_docx_archive(data)
    return ext


def content_disposition(filename: str) -> str:
    ascii_name = re.sub(r"[^A-Za-z0-9 ._()\-]", "_", filename) or "file"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


async def store_attachment(
    db: AsyncSession,
    request: Request,
    cipher: FieldCipher,
    settings: Settings,
    user_id: uuid.UUID,
    app: Application,
    *,
    filename: str,
    mime: str,
    data: bytes,
    event_id: uuid.UUID | None = None,
) -> Attachment:
    """Encrypt and store a file on an application, within the user's quota. Caller commits."""
    used = await db.scalar(
        select(func.coalesce(func.sum(Attachment.size_bytes), 0)).where(
            Attachment.user_id == user_id
        )
    )
    if int(used or 0) + len(data) > settings.attachment_quota_bytes:
        raise ApiError(
            413,
            "storage_quota",
            "You've reached your attachment storage limit. Delete some files first.",
        )
    attachment = Attachment(
        id=uuid.uuid4(),
        user_id=user_id,
        application_id=app.id,
        event_id=event_id,
        filename=filename,
        mime=mime,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )
    attachment.content_enc = cipher.encrypt_bytes(
        data, service.attachment_context(user_id, attachment.id)
    )
    db.add(attachment)
    app.updated_at = utcnow()
    audit.record(
        db,
        request,
        "tracker.attachment_uploaded",
        user_id,
        attachment_id=str(attachment.id),
        application_id=str(app.id),
        mime=mime,
        size_bytes=len(data),
    )
    return attachment


@router.post("/applications/{application_id}/attachments", status_code=201)
async def upload_attachment(
    application_id: uuid.UUID,
    request: Request,
    auth: CurrentUser,
    db: DB,
    cipher: CipherDep,
    settings: SettingsDep,
    event_id: uuid.UUID | None = None,
    x_filename: str | None = Header(default=None, max_length=1000),
) -> AttachmentOut:
    uid = auth.user.id
    app = await service.get_owned(db, Application, uid, application_id)
    if event_id is not None:
        event = await service.get_owned(db, ApplicationEvent, uid, event_id, "Event not found.")
        if event.application_id != app.id:
            raise service.not_found("Event not found.")
    await check_and_hit(db, f"attachment:{uid}", UPLOADS_PER_HOUR, timedelta(hours=1))
    await db.commit()  # count the attempt even if the file is rejected

    data = await request.body()
    if not data:
        raise ApiError(422, "empty_file", "The file is empty.")
    try:
        ext = sniff_attachment(data, request.headers.get("content-type", ""))
    except RejectedFile as exc:
        raise ApiError(exc.status, exc.code, exc.message) from None

    attachment = await store_attachment(
        db,
        request,
        cipher,
        settings,
        uid,
        app,
        filename=clean_filename(unquote(x_filename or ""), ext),
        mime=ALLOWED[ext],
        data=data,
        event_id=event_id,
    )
    await db.commit()
    await db.refresh(attachment)
    return service.attachment_out(attachment)


@router.get("/attachments/{attachment_id}/download")
async def download_attachment(
    attachment_id: uuid.UUID, auth: CurrentUser, db: DB, cipher: CipherDep
) -> Response:
    uid = auth.user.id
    attachment = await service.get_owned(db, Attachment, uid, attachment_id)
    content = cipher.decrypt_bytes(
        attachment.content_enc, service.attachment_context(uid, attachment.id)
    )
    return Response(
        content,
        media_type=attachment.mime,
        headers={
            "Content-Disposition": content_disposition(attachment.filename),
        },
    )


@router.delete("/attachments/{attachment_id}", status_code=204)
async def delete_attachment(
    attachment_id: uuid.UUID, request: Request, auth: CurrentUser, db: DB
) -> Response:
    uid = auth.user.id
    attachment = await service.get_owned(db, Attachment, uid, attachment_id)
    await db.delete(attachment)
    audit.record(db, request, "tracker.attachment_deleted", uid, attachment_id=str(attachment.id))
    await db.commit()
    return Response(status_code=204)
