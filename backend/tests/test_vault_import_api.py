# SPDX-License-Identifier: Apache-2.0
"""Resume import API tests. Every resume here is fictional and built at test time."""

from __future__ import annotations

from collections.abc import Callable

import httpx
from resume_fixtures import PDF_LINES, make_docx, make_pdf, make_zip
from sqlalchemy import select

from waypoint import db as dbmod
from waypoint.config import Settings
from waypoint.models import AuditLog, ResumeImport
from waypoint.vault.importer.guards import DOCX_MIME, PDF_MIME

IMPORTS = "/api/v1/vault/imports"
V = "/api/v1/vault"


async def _upload(
    c: httpx.AsyncClient, data: bytes, mime: str, filename: str = "resume"
) -> httpx.Response:
    return await c.post(
        IMPORTS, content=data, headers={"content-type": mime, "x-filename": filename}
    )


async def test_import_docx_creates_unverified_drafts(user_client: httpx.AsyncClient) -> None:
    c = user_client
    r = await _upload(c, make_docx(), DOCX_MIME, "Casey%20Lin%20resume.docx")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["filename"] == "Casey Lin resume.docx"
    assert body["mime"] == DOCX_MIME
    assert body["stats"]["kinds"] == {
        "role": 2,
        "achievement": 1,
        "responsibility": 2,
        "education": 1,
        "skill": 4,
    }
    assert body["stats"]["sections"] == ["experience", "education", "skills"]
    assert body["drafts_remaining"] == 10
    assert body["suggestions"]["full_name"] == "Casey Lin"
    assert body["suggestions"]["email"] == "casey@example.com"
    assert "Fabrikam Services" in body["text"]

    entries = body["entries"]
    # Drafts come back in the order they appear in the resume.
    assert [e["kind"] for e in entries] == [
        "role",
        "achievement",
        "responsibility",
        "role",
        "responsibility",
        "education",
        "skill",
        "skill",
        "skill",
        "skill",
    ]
    assert all(e["verified"] is False and e["source"] == "import" for e in entries)
    assert all(e["import_id"] == body["id"] for e in entries)
    roles = {e["id"]: e for e in entries if e["kind"] == "role"}
    bullets = [e for e in entries if e["kind"] in {"achievement", "responsibility"}]
    assert all(b["parent_id"] in roles for b in bullets)

    # Drafts show up in the vault and in the review count.
    summary = (await c.get(f"{V}/summary")).json()
    assert summary["needs_review"] == 10
    assert len((await c.get(f"{V}/entries?verified=false")).json()) == 10


async def test_import_pdf(user_client: httpx.AsyncClient) -> None:
    r = await _upload(user_client, make_pdf(PDF_LINES), PDF_MIME, "riley.pdf")
    assert r.status_code == 201, r.text
    kinds = r.json()["stats"]["kinds"]
    assert kinds == {"role": 1, "achievement": 1, "responsibility": 1, "skill": 3}


async def test_review_flow_approve_and_delete_import(user_client: httpx.AsyncClient) -> None:
    c = user_client
    body = (await _upload(c, make_docx(), DOCX_MIME)).json()
    imp_id = body["id"]
    role = next(e for e in body["entries"] if e["kind"] == "role")
    bullet = next(e for e in body["entries"] if e["parent_id"] == role["id"])
    skill = next(e for e in body["entries"] if e["kind"] == "skill")

    # Edit one draft, approve two, discard one.
    r = await c.patch(f"{V}/entries/{skill['id']}", json={"data": {"name": "Windows Server"}})
    assert r.status_code == 200
    r = await c.post(
        f"{V}/entries/verify", json={"ids": [role["id"], bullet["id"]], "verified": True}
    )
    assert r.json() == {"updated": 2}
    assert (await c.get(f"{V}/entries/{bullet['id']}")).json()["usable"] is True
    other_skill = [e for e in body["entries"] if e["kind"] == "skill"][1]
    assert (await c.delete(f"{V}/entries/{other_skill['id']}")).status_code == 204

    detail = (await c.get(f"{IMPORTS}/{imp_id}")).json()
    assert detail["drafts_remaining"] == 7
    listed = (await c.get(IMPORTS)).json()
    assert [(i["id"], i["drafts_remaining"]) for i in listed] == [(imp_id, 7)]

    # Deleting the import with discard removes remaining drafts but keeps approved ones.
    r = await c.delete(f"{IMPORTS}/{imp_id}", params={"discard_drafts": "true"})
    assert r.status_code == 204
    left = (await c.get(f"{V}/entries")).json()
    assert {e["id"] for e in left} == {role["id"], bullet["id"]}
    assert all(e["import_id"] is None for e in left)
    assert (await c.get(f"{IMPORTS}/{imp_id}")).status_code == 404

    async for s in dbmod.get_db():
        assert await s.scalar(select(ResumeImport)) is None
        events = [a.event for a in await s.scalars(select(AuditLog).order_by(AuditLog.id))]
    assert "vault.import" in events and "vault.import_deleted" in events


async def test_discard_keeps_draft_parent_of_verified_child(user_client: httpx.AsyncClient) -> None:
    c = user_client
    body = (await _upload(c, make_docx(), DOCX_MIME)).json()
    role = next(e for e in body["entries"] if e["kind"] == "role")
    bullet = next(e for e in body["entries"] if e["parent_id"] == role["id"])
    await c.post(f"{V}/entries/verify", json={"ids": [bullet["id"]], "verified": True})
    await c.delete(f"{IMPORTS}/{body['id']}", params={"discard_drafts": "true"})
    ids = {e["id"] for e in (await c.get(f"{V}/entries")).json()}
    assert ids == {role["id"], bullet["id"]}


async def test_delete_import_without_discard_keeps_drafts(user_client: httpx.AsyncClient) -> None:
    c = user_client
    body = (await _upload(c, make_docx(), DOCX_MIME)).json()
    await c.delete(f"{IMPORTS}/{body['id']}")
    left = (await c.get(f"{V}/entries?verified=false")).json()
    assert len(left) == 10
    assert all(e["import_id"] is None and e["source"] == "import" for e in left)


async def test_extracted_text_is_encrypted_and_file_not_stored(
    user_client: httpx.AsyncClient,
) -> None:
    await _upload(user_client, make_docx(), DOCX_MIME)
    async for s in dbmod.get_db():
        row = await s.scalar(select(ResumeImport))
    assert row is not None
    assert row.text_enc.startswith("v1:") and "Fabrikam" not in row.text_enc
    assert "casey@example.com" not in row.suggestions_enc
    assert len(row.sha256) == 64 and row.size_bytes > 0


async def test_rejects_wrong_types(user_client: httpx.AsyncClient) -> None:
    c = user_client
    cases = [
        (b"<html><script>alert(1)</script>", "text/html", 415),
        (make_pdf(["x"]), DOCX_MIME, 415),  # declared type doesn't match content
        (make_docx(), PDF_MIME, 415),
        (b"MZ\x90\x00\x03", "application/pdf", 415),
        (make_zip({"xl/workbook.xml": b"x"}), DOCX_MIME, 415),
        (b"", PDF_MIME, 422),
    ]
    for data, mime, status in cases:
        r = await _upload(c, data, mime)
        assert r.status_code == status, (mime, r.text)
    assert (await c.get(IMPORTS)).json() == []


async def test_rejects_zip_bomb_and_corrupt_files(user_client: httpx.AsyncClient) -> None:
    c = user_client
    bomb = make_zip({"word/document.xml": b"\0" * (4 * 1024 * 1024)})
    r = await _upload(c, bomb, DOCX_MIME)
    assert r.status_code == 422
    assert r.json()["error"] == {
        "code": "unreadable_file",
        "message": "We couldn't read this file.",
    }
    r = await _upload(c, b"%PDF-1.7\n" + b"\x00" * 100, PDF_MIME)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "unreadable_file"


async def test_upload_size_limits(
    app_factory: Callable[[Settings], httpx.AsyncClient],
    make_settings: Callable[..., Settings],
) -> None:
    s = make_settings(import_max_bytes=200_000, max_body_bytes=50_000)
    c = app_factory(s)
    await c.get("/api/v1/auth/status")
    await c.post(
        "/api/v1/auth/register",
        json={"email": "jordan@example.com", "password": "x" * 8 + "yz1234", "display_name": "J"},
    )
    big = b"%PDF-1.4\n" + b"0" * 300_000
    r = await _upload(c, big, PDF_MIME)
    assert r.status_code == 413
    # Between the two limits: allowed on the import route only.
    mid = b"%PDF-1.4\n" + b"0" * 100_000
    assert (await _upload(c, mid, PDF_MIME)).status_code == 422  # reached the parser
    r = await c.put(f"{V}/profile", content=b"{" + b" " * 100_000 + b"}")
    assert r.status_code == 413


async def test_import_rate_limit(user_client: httpx.AsyncClient) -> None:
    for _ in range(20):
        await _upload(user_client, b"nope", PDF_MIME)
    r = await _upload(user_client, make_docx(), DOCX_MIME)
    assert r.status_code == 429


async def test_import_isolation(two_users: tuple[httpx.AsyncClient, httpx.AsyncClient]) -> None:
    a, b = two_users
    body = (await _upload(a, make_docx(), DOCX_MIME)).json()
    assert (await b.get(IMPORTS)).json() == []
    assert (await b.get(f"{IMPORTS}/{body['id']}")).status_code == 404
    assert (await b.delete(f"{IMPORTS}/{body['id']}")).status_code == 404
    assert (await b.get(f"{V}/entries?import_id={body['id']}")).json() == []
    assert (await a.get(f"{IMPORTS}/{body['id']}")).status_code == 200


async def test_import_requires_csrf(user_client: httpx.AsyncClient) -> None:
    r = await user_client.post(
        IMPORTS,
        content=make_docx(),
        headers={"content-type": DOCX_MIME, "x-csrf-token": "forged"},
    )
    assert r.status_code == 403
