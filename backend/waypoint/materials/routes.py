# SPDX-License-Identifier: Apache-2.0
"""Materials API: generate, review, fact-check, diff, export, attach."""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from waypoint import audit
from waypoint.ai.facts import FactSet, load_facts
from waypoint.ai.jobs import allow_private_for
from waypoint.ai.prompting import untrusted
from waypoint.ai.providers import AiError
from waypoint.ai.service import client_for, model_for, structured, to_api_error
from waypoint.auth.sessions import DB, CipherDep, CurrentUser, SettingsDep, csrf_protect
from waypoint.discovery.models import JobPosting
from waypoint.errors import ApiError
from waypoint.materials import build, export
from waypoint.materials.models import Document
from waypoint.models import VaultEntry, VaultProfile
from waypoint.security import contexts
from waypoint.security.crypto import FieldCipher
from waypoint.tracker.files import content_disposition, store_attachment
from waypoint.tracker.models import Application
from waypoint.vault.schemas import Strict

router = APIRouter(
    prefix="/api/v1/materials", tags=["materials"], dependencies=[Depends(csrf_protect)]
)


class GenerateIn(Strict):
    kind: Literal["resume", "cover_letter"]
    method: Literal["rules", "ai"] = "rules"
    posting_id: uuid.UUID | None = None
    application_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _one_target(self) -> GenerateIn:
        if self.posting_id and self.application_id:
            raise ValueError("choose a posting or an application, not both")
        if self.kind == "cover_letter" and not (self.posting_id or self.application_id):
            raise ValueError("a cover letter needs a job to write to")
        return self


class ItemPatch(Strict):
    action: Literal["approve", "edit", "remove"]
    text: str = Field(default="", max_length=2000)


class DocPatch(Strict):
    title: str = Field(min_length=1, max_length=200)


class AttachIn(Strict):
    format: Literal["docx", "pdf"] = "pdf"
    application_id: uuid.UUID | None = None


class DocSummary(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    is_master: bool
    posting_id: uuid.UUID | None
    application_id: uuid.UUID | None
    target_title: str
    target_company: str
    method: str
    model: str
    flagged: int
    stale: bool
    created_at: datetime
    updated_at: datetime
    exported_at: datetime | None


class DocOut(DocSummary):
    content: dict[str, Any]
    sources: dict[str, str]


def _summary(d: Document, digest: str) -> dict[str, Any]:
    return {
        "id": d.id,
        "kind": d.kind,
        "title": d.title,
        "is_master": d.is_master,
        "posting_id": d.posting_id,
        "application_id": d.application_id,
        "target_title": d.target_title,
        "target_company": d.target_company,
        "method": d.method,
        "model": d.model,
        "flagged": build.flagged_count(d.kind, d.content),
        "stale": bool(d.vault_hash) and d.vault_hash != digest,
        "created_at": d.created_at,
        "updated_at": d.updated_at,
        "exported_at": d.exported_at,
    }


async def _profile_summary(db: AsyncSession, uid: uuid.UUID) -> str:
    profile = await db.get(VaultProfile, uid)
    return profile.summary if profile else ""


async def _get(db: AsyncSession, uid: uuid.UUID, doc_id: uuid.UUID) -> Document:
    doc = await db.scalar(select(Document).where(Document.user_id == uid, Document.id == doc_id))
    if doc is None:
        raise ApiError(404, "not_found", "Not found.")
    return doc


async def _target(
    db: AsyncSession, uid: uuid.UUID, body: GenerateIn
) -> tuple[build.Target | None, JobPosting | None, Application | None]:
    if body.posting_id:
        p = await db.scalar(
            select(JobPosting).where(JobPosting.user_id == uid, JobPosting.id == body.posting_id)
        )
        if p is None:
            raise ApiError(404, "not_found", "That job wasn't found.")
        return build.Target(p.title, p.company, p.description), p, None
    if body.application_id:
        a = await db.scalar(
            select(Application).where(
                Application.user_id == uid, Application.id == body.application_id
            )
        )
        if a is None:
            raise ApiError(404, "not_found", "That application wasn't found.")
        return build.Target(a.title, a.company, a.description), None, a
    return None, None, None


def _target_block(t: build.Target) -> str:
    return untrusted("job_posting", f"Title: {t.title}\nCompany: {t.company}\n\n{t.description}")


async def _out(
    db: AsyncSession, uid: uuid.UUID, doc: Document, factset: FactSet | None = None
) -> DocOut:
    factset = factset or await load_facts(db, uid)
    sources = {f.id: f.text for f in factset.facts}
    summary = await _profile_summary(db, uid)
    if summary:
        sources[build.PROFILE_FACT] = "Your profile summary"
    # Facts cited but no longer usable (unverified or deleted since) still get a label.
    for i in build.all_items(doc.kind, doc.content):
        for fid in i.get("fact_ids", []):
            sources.setdefault(fid, "A fact that is no longer verified")
    return DocOut(**_summary(doc, factset.digest()), content=doc.content, sources=sources)


@router.get("/documents")
async def list_documents(
    auth: CurrentUser,
    db: DB,
    posting_id: uuid.UUID | None = None,
    application_id: uuid.UUID | None = None,
) -> list[DocSummary]:
    uid = auth.user.id
    stmt = select(Document).where(Document.user_id == uid).order_by(Document.created_at.desc())
    if posting_id:
        stmt = stmt.where(Document.posting_id == posting_id)
    if application_id:
        stmt = stmt.where(Document.application_id == application_id)
    digest = (await load_facts(db, uid)).digest()
    return [DocSummary(**_summary(d, digest)) for d in await db.scalars(stmt.limit(500))]


@router.post("/documents", status_code=201)
async def generate(
    body: GenerateIn,
    request: Request,
    auth: CurrentUser,
    db: DB,
    cipher: CipherDep,
    settings: SettingsDep,
) -> DocOut:
    uid = auth.user.id
    factset = await load_facts(db, uid)
    if not factset.facts:
        raise ApiError(409, "empty_vault", "Add and verify some facts in your Career Vault first.")
    target, posting, application = await _target(db, uid, body)
    profile_summary = await _profile_summary(db, uid)
    model = ""
    try:
        if body.method == "ai":
            if target is None:
                raise ApiError(
                    422,
                    "target_required",
                    "AI tailoring needs a job. The master resume is built from your vault.",
                )
            ai = await client_for(
                db,
                uid,
                cipher,
                allow_private=await allow_private_for(db, uid, settings.outbound_allow_private),
            )
            feature = "resume" if body.kind == "resume" else "cover_letter"
            model = model_for(ai.settings, feature)
            subject = {
                "posting_id": str(posting.id) if posting else None,
                "application_id": str(application.id) if application else None,
            }
            if body.kind == "resume":
                out = await structured(
                    ai,
                    feature=feature,
                    prompt_name="resume_tailor",
                    values={"facts": factset.prompt_json(), "posting": _target_block(target)},
                    schema_model=build.TailorOut,
                    max_tokens=8000,
                    effort="medium",
                    subject=subject,
                )
                content = build.build_resume_from_ai(factset, profile_summary, out)
            else:
                letter = await structured(
                    ai,
                    feature=feature,
                    prompt_name="cover_letter",
                    values={"facts": factset.prompt_json(), "posting": _target_block(target)},
                    schema_model=build.LetterOut,
                    max_tokens=4000,
                    effort="medium",
                    subject=subject,
                )
                content = build.build_letter_from_ai(factset, target, letter)
        elif body.kind == "resume":
            content = build.build_resume_rules(factset, profile_summary, target)
        else:
            assert target is not None  # noqa: S101  (validated above)
            content = build.build_letter_rules(factset, target)
    except AiError as exc:
        raise to_api_error(exc) from None

    label = "Resume" if body.kind == "resume" else "Cover letter"
    title = (
        f"{label} for {target.title}" + (f" at {target.company}" if target.company else "")
        if target
        else "Master resume"
    )
    doc = Document(
        user_id=uid,
        kind=body.kind,
        title=title[:200],
        is_master=target is None,
        posting_id=posting.id if posting else None,
        application_id=application.id
        if application
        else (posting.application_id if posting else None),
        target_title=target.title if target else "",
        target_company=target.company if target else "",
        method=body.method,
        model=model,
        content=content,
        vault_hash=factset.digest(),
    )
    db.add(doc)
    await db.flush()
    audit.record(
        db,
        request,
        "document.generated",
        uid,
        document_id=str(doc.id),
        kind=body.kind,
        method=body.method,
        flagged=build.flagged_count(body.kind, content),
    )
    await db.commit()
    await db.refresh(doc)
    return await _out(db, uid, doc, factset)


@router.get("/documents/{doc_id}")
async def get_document(doc_id: uuid.UUID, auth: CurrentUser, db: DB) -> DocOut:
    return await _out(db, auth.user.id, await _get(db, auth.user.id, doc_id))


@router.patch("/documents/{doc_id}")
async def rename_document(doc_id: uuid.UUID, body: DocPatch, auth: CurrentUser, db: DB) -> DocOut:
    doc = await _get(db, auth.user.id, doc_id)
    doc.title = body.title.strip()
    await db.commit()
    await db.refresh(doc)
    return await _out(db, auth.user.id, doc)


def _find(
    content: dict[str, Any], kind: str, item_id: str
) -> tuple[list[dict[str, Any]], int] | None:
    containers: list[list[dict[str, Any]]] = []
    if kind == "cover_letter":
        containers.append(content.get("paragraphs", []))
    else:
        if content.get("summary") and content["summary"]["id"] == item_id:
            return None
        for section in ("experience", "projects"):
            containers += [e["bullets"] for e in content.get(section, [])]
    for container in containers:
        for i, it in enumerate(container):
            if it["id"] == item_id:
                return container, i
    raise ApiError(404, "not_found", "That item wasn't found.")


@router.patch("/documents/{doc_id}/items/{item_id}")
async def patch_item(
    doc_id: uuid.UUID, item_id: str, body: ItemPatch, auth: CurrentUser, db: DB
) -> DocOut:
    """Approve a flagged item, rewrite it (your own words count as approved), or remove it."""
    uid = auth.user.id
    doc = await _get(db, uid, doc_id)
    content = copy.deepcopy(doc.content)
    found = _find(content, doc.kind, item_id)
    target = content["summary"] if found is None else found[0][found[1]]
    if body.action == "remove":
        if found is None:
            content["summary"] = None
        else:
            found[0].pop(found[1])
    elif body.action == "approve":
        target["status"], target["reason"] = build.APPROVED, "Approved by you."
    else:
        text = body.text.strip()
        if not text:
            raise ApiError(422, "text_required", "Write the new text, or remove the item.")
        target["text"] = text
        # Re-check the new wording; if it still fails, it's the user's own statement.
        status, _ = build.check(
            text,
            target.get("fact_ids", []),
            build.known_facts(await load_facts(db, uid), await _profile_summary(db, uid)),
            allow_uncited=doc.kind == "cover_letter",
        )
        target["status"], target["reason"] = (
            (status, "") if status == build.LINKED else (build.APPROVED, "Written by you.")
        )
    doc.content = content
    flag_modified(doc, "content")  # nested JSON edits aren't detected otherwise
    await db.commit()
    await db.refresh(doc)
    return await _out(db, uid, doc)


@router.delete("/documents/{doc_id}", status_code=204)
async def delete_document(doc_id: uuid.UUID, auth: CurrentUser, db: DB) -> Response:
    doc = await _get(db, auth.user.id, doc_id)
    await db.delete(doc)
    await db.commit()
    return Response(status_code=204)


@router.get("/documents/{doc_id}/diff")
async def diff(doc_id: uuid.UUID, auth: CurrentUser, db: DB) -> dict[str, Any]:
    """Compare a resume with the master resume built from the vault right now."""
    uid = auth.user.id
    doc = await _get(db, uid, doc_id)
    if doc.kind != "resume":
        raise ApiError(422, "not_a_resume", "Only resumes can be compared with the master resume.")
    master = build.build_resume_rules(
        await load_facts(db, uid), await _profile_summary(db, uid), None
    )
    ops = build.diff_lines(
        build.plain_lines("resume", master), build.plain_lines("resume", doc.content)
    )
    return {
        "ops": ops,
        "added": sum(o["op"] == "added" for o in ops),
        "removed": sum(o["op"] == "removed" for o in ops),
    }


async def _header(
    db: AsyncSession, uid: uuid.UUID, cipher: FieldCipher, display_name: str
) -> export.Header:
    profile = await db.get(VaultProfile, uid)
    contact: list[str] = []
    name, headline = display_name, ""
    if profile:
        name = profile.full_name or display_name
        headline = profile.headline
        for field_name in ("email", "phone"):
            enc = getattr(profile, f"{field_name}_enc")
            if enc:
                contact.append(cipher.decrypt(enc, contexts.profile(uid, field_name)))
        if profile.location_text:
            contact.append(profile.location_text)
    links = await db.scalars(
        select(VaultEntry).where(
            VaultEntry.user_id == uid, VaultEntry.kind == "link", VaultEntry.verified.is_(True)
        )
    )
    contact += [str(link.data.get("url", "")).removeprefix("https://") for link in links][:3]
    return export.Header(name=name, headline=headline, contact=[c for c in contact if c])


async def _render(
    db: AsyncSession,
    uid: uuid.UUID,
    cipher: FieldCipher,
    display_name: str,
    doc: Document,
    fmt: str,
) -> tuple[bytes, str, str]:
    flagged = build.flagged_count(doc.kind, doc.content)
    if flagged:
        raise ApiError(
            409,
            "unverified_content",
            f"{flagged} flagged item{'s' if flagged != 1 else ''} must be reviewed before export.",
        )
    header = await _header(db, uid, cipher, display_name)
    today = datetime.now(UTC).date()
    if fmt == "docx":
        data = (
            export.resume_docx(doc.content, header)
            if doc.kind == "resume"
            else export.letter_docx(doc.content, header, today)
        )
        mime = export.DOCX_MIME
    else:
        data = (
            export.resume_pdf(doc.content, header)
            if doc.kind == "resume"
            else export.letter_pdf(doc.content, header, today)
        )
        mime = export.PDF_MIME
    return data, mime, export.filename(header, doc.kind, doc.target_company, fmt)


@router.get("/documents/{doc_id}/export")
async def export_document(
    doc_id: uuid.UUID,
    request: Request,
    auth: CurrentUser,
    db: DB,
    cipher: CipherDep,
    format: Literal["docx", "pdf"] = Query(default="pdf"),
) -> Response:
    uid = auth.user.id
    doc = await _get(db, uid, doc_id)
    data, mime, name = await _render(db, uid, cipher, auth.user.display_name, doc, format)
    doc.exported_at = datetime.now(UTC)
    audit.record(db, request, "document.exported", uid, document_id=str(doc.id), format=format)
    await db.commit()
    return Response(
        data, media_type=mime, headers={"Content-Disposition": content_disposition(name)}
    )


@router.post("/documents/{doc_id}/attach", status_code=201)
async def attach_document(
    doc_id: uuid.UUID,
    body: AttachIn,
    request: Request,
    auth: CurrentUser,
    db: DB,
    cipher: CipherDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """Save the exported file on the application in the tracker (what you sent)."""
    uid = auth.user.id
    doc = await _get(db, uid, doc_id)
    app_id = body.application_id or doc.application_id
    if app_id is None:
        raise ApiError(
            422, "application_required", "Track this job first, then attach the file to it."
        )
    app = await db.scalar(
        select(Application).where(Application.user_id == uid, Application.id == app_id)
    )
    if app is None:
        raise ApiError(404, "not_found", "That application wasn't found.")
    data, mime, name = await _render(db, uid, cipher, auth.user.display_name, doc, body.format)
    attachment = await store_attachment(
        db, request, cipher, settings, uid, app, filename=name, mime=mime, data=data
    )
    doc.application_id = app.id
    doc.exported_at = datetime.now(UTC)
    audit.record(
        db,
        request,
        "document.exported",
        uid,
        document_id=str(doc.id),
        format=body.format,
        attached=True,
    )
    await db.commit()
    return {"attachment_id": str(attachment.id), "application_id": str(app.id), "filename": name}
