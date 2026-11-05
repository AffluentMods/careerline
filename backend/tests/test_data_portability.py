# SPDX-License-Identifier: Apache-2.0
"""Full export, import into another account, re-authentication, and hostile archives."""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, update

from careerline import db
from careerline.config import Settings
from careerline.models import AuthSession, VaultProfile
from careerline.tracker.models import Contact
from tests.conftest import TEST_PASSWORD
from tests.resume_fixtures import make_pdf

V = "/api/v1/vault"
T = "/api/v1/tracker"


async def _seed(c: httpx.AsyncClient) -> dict[str, Any]:
    await c.put(
        f"{V}/profile",
        json={"full_name": "Jordan Rivera", "headline": "Analyst", "email": "jordan@example.com"},
    )
    role = (
        await c.post(
            f"{V}/entries",
            json={"kind": "role", "data": {"title": "Analyst", "employer": "Northwind Tools"}},
        )
    ).json()
    ach = (
        await c.post(
            f"{V}/entries",
            json={
                "kind": "achievement",
                "parent_id": role["id"],
                "data": {"text": "Cut report time by 40%"},
            },
        )
    ).json()
    await c.post(f"{V}/entries", json={"kind": "skill", "data": {"name": "Python"}})
    app = (
        await c.post(f"{T}/applications", json={"title": "Data Analyst", "company": "Litware"})
    ).json()
    await c.post(f"{T}/applications/{app['id']}/stage", json={"stage": "applied"})
    contact = (
        await c.post(
            f"{T}/contacts",
            json={"name": "Sam Example", "email": "sam@example.com", "phone": "555-0100"},
        )
    ).json()
    pdf = make_pdf(["Resume of Jordan"])
    r = await c.post(
        f"{T}/applications/{app['id']}/attachments",
        content=pdf,
        headers={"content-type": "application/pdf", "x-filename": "resume.pdf"},
    )
    assert r.status_code == 201, r.text
    posting = (
        await c.post(
            "/api/v1/discovery/import",
            json={"title": "Report Analyst", "company": "Litware", "text": "Python and SQL."},
        )
    ).json()
    doc = (
        await c.post(
            "/api/v1/materials/documents",
            json={"kind": "resume", "method": "rules", "posting_id": posting["id"]},
        )
    ).json()
    await c.put("/api/v1/ai/settings", json={"provider": "anthropic", "api_key": "sk-ant-secret"})
    return {"role": role, "ach": ach, "app": app, "contact": contact, "pdf": pdf, "doc": doc}


async def _export(c: httpx.AsyncClient) -> zipfile.ZipFile:
    r = await c.post("/api/v1/data/export")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers["content-disposition"]
    return zipfile.ZipFile(io.BytesIO(r.content))


def _table(zf: zipfile.ZipFile, name: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = json.loads(zf.read(f"tables/{name}.json"))
    return rows


async def test_export_contents(user_client: httpx.AsyncClient) -> None:
    seeded = await _seed(user_client)
    zf = await _export(user_client)
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["format"] == "careerline-export"
    assert manifest["user"]["email"] == "jordan@example.com"
    # Encrypted fields come out readable; secrets don't come out at all.
    contacts = _table(zf, "contacts")
    assert contacts[0]["email"] == "sam@example.com" and "email_enc" not in contacts[0]
    assert _table(zf, "vault_profiles")[0]["email"] == "jordan@example.com"
    ai = _table(zf, "ai_settings")[0]
    assert ai["provider"] == "anthropic" and "api_key_enc" not in ai
    everything = b"".join(zf.read(n) for n in zf.namelist())
    assert b"sk-ant-secret" not in everything
    assert b"password_hash" not in everything and b"mfa_secret" not in everything
    for name in zf.namelist():
        if name.startswith("tables/"):
            assert all("user_id" not in row for row in json.loads(zf.read(name)))
    attachment = _table(zf, "attachments")[0]
    assert zf.read(f"files/{attachment['id']}") == seeded["pdf"]


async def test_export_and_import_need_recent_password(
    user_client: httpx.AsyncClient, settings: Settings
) -> None:
    async with db.get_sessionmaker()() as s:
        await s.execute(
            update(AuthSession).values(reauth_at=AuthSession.created_at - timedelta(hours=1))
        )
        await s.commit()
    r = await user_client.post("/api/v1/data/export")
    assert r.status_code == 403 and r.json()["error"]["code"] == "reauth_required"
    r = await user_client.post("/api/v1/data/import", content=b"x")
    assert r.status_code == 403
    r = await user_client.post("/api/v1/auth/reauth", json={"password": "wrong password!!"})
    assert r.status_code == 401
    r = await user_client.post("/api/v1/auth/reauth", json={"password": TEST_PASSWORD})
    assert r.status_code == 200
    assert (await user_client.post("/api/v1/data/export")).status_code == 200


async def test_import_into_another_account(
    two_users: tuple[httpx.AsyncClient, httpx.AsyncClient],
) -> None:
    a, b = two_users
    seeded = await _seed(a)
    r = await a.post("/api/v1/data/export")
    archive = r.content

    r = await b.post(
        "/api/v1/data/import", content=archive, headers={"content-type": "application/zip"}
    )
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["imported"]["vault_entries"] == 3
    assert result["imported"]["attachments"] == 1
    assert any("API key" in n for n in result["notes"])

    # Same data, fresh ids, owned by b.
    entries = (await b.get(f"{V}/entries")).json()
    assert {
        e["data"].get("title") or e["data"].get("text") or e["data"].get("name") for e in entries
    } == {
        "Analyst",
        "Cut report time by 40%",
        "Python",
    }
    ids = {e["id"] for e in entries}
    assert seeded["role"]["id"] not in ids
    child = next(e for e in entries if e["kind"] == "achievement")
    assert child["parent_id"] in ids  # the parent link was rewritten too

    contacts = (await b.get(f"{T}/contacts")).json()
    assert contacts[0]["email"] == "sam@example.com"  # re-encrypted with b's context

    apps = (await b.get(f"{T}/applications")).json()
    detail = (await b.get(f"{T}/applications/{apps[0]['id']}")).json()
    attachment = detail["attachments"][0]
    file = await b.get(f"{T}/attachments/{attachment['id']}/download")
    assert file.status_code == 200 and file.content == seeded["pdf"]

    # References inside JSON (the facts a resume cites) point at b's new vault ids.
    docs = (await b.get("/api/v1/materials/documents")).json()
    doc = (await b.get(f"/api/v1/materials/documents/{docs[0]['id']}")).json()
    cited = {
        f
        for exp in doc["content"]["experience"]
        for item in exp["bullets"]
        for f in item["fact_ids"]
    }
    assert cited and cited <= ids
    assert doc["id"] != seeded["doc"]["id"]

    # Imported sources and keys need attention; nothing of a's is visible to b's AI settings.
    ai = (await b.get("/api/v1/ai/settings")).json()
    assert ai["provider"] == "anthropic" and ai["has_api_key"] is False

    # a's data is untouched and still a's.
    assert len((await a.get(f"{V}/entries")).json()) == 3

    # Importing again into the now non-empty account is refused, and changes nothing.
    r = await b.post("/api/v1/data/import", content=archive)
    assert r.status_code == 422 and "empty account" in r.json()["error"]["message"]
    assert len((await b.get(f"{V}/entries")).json()) == 3


def _archive(tables: dict[str, list[dict[str, Any]]], **manifest: Any) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format": "careerline-export",
                    "version": 1,
                    "user_id": str(uuid.uuid4()),
                    **manifest,
                }
            ),
        )
        for name, rows in tables.items():
            zf.writestr(f"tables/{name}.json", json.dumps(rows))
        zf.writestr("../../evil.txt", "ignored")
    return buf.getvalue()


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"not a zip", "isn't a CareerLine export"),
        (_archive({}, format="something-else"), "isn't a CareerLine export"),
        (_archive({}, version=99), "newer version"),
        (_archive({"users": []}), "unknown table"),
        (
            _archive(
                {
                    "vault_entries": [
                        {"id": str(uuid.uuid4()), "kind": "skill", "data": {}, "is_admin": True}
                    ]
                }
            ),
            "unknown field",
        ),
        (
            _archive(
                {
                    "vault_entries": [
                        {
                            "id": str(uuid.uuid4()),
                            "kind": "skill",
                            "data": {"name": "x"},
                            "parent_id": str(uuid.uuid4()),
                        }
                    ]
                }
            ),
            "isn't in the export",
        ),
        (
            _archive({"applications": [{"id": str(uuid.uuid4()), "title": 5}]}),
            "title must be text",
        ),
        (
            _archive(
                {"applications": [{"id": "same", "title": "a"}, {"id": "same", "title": "b"}]}
            ),
            "repeated id",
        ),
    ],
)
async def test_hostile_archives_are_rejected(
    user_client: httpx.AsyncClient, body: bytes, message: str
) -> None:
    r = await user_client.post("/api/v1/data/import", content=body)
    assert r.status_code == 422, r.text
    assert message in r.json()["error"]["message"]
    assert (await user_client.get(f"{T}/applications")).json() == []


async def test_import_cannot_reach_other_users(
    two_users: tuple[httpx.AsyncClient, httpx.AsyncClient],
) -> None:
    """An archive naming another user's ids can't touch their rows."""
    a, b = two_users
    victim = (
        await a.post(f"{T}/contacts", json={"name": "Victim", "email": "v@example.com"})
    ).json()
    archive = _archive(
        {
            "contacts": [{"id": victim["id"], "name": "Hijacked", "email": "x@example.com"}],
        }
    )
    r = await b.post("/api/v1/data/import", content=archive)
    assert r.status_code == 200, r.text
    mine = (await b.get(f"{T}/contacts")).json()
    assert [c["name"] for c in mine] == ["Hijacked"] and mine[0]["id"] != victim["id"]
    theirs = (await a.get(f"{T}/contacts")).json()
    assert [c["name"] for c in theirs] == ["Victim"]


async def test_data_routes_need_sign_in(client: httpx.AsyncClient) -> None:
    assert (await client.post("/api/v1/data/export")).status_code == 401
    assert (await client.post("/api/v1/data/import", content=b"x")).status_code == 401


async def test_contacts_stay_encrypted_after_import(
    two_users: tuple[httpx.AsyncClient, httpx.AsyncClient],
) -> None:
    a, b = two_users
    await _seed(a)
    archive = (await a.post("/api/v1/data/export")).content
    await b.post("/api/v1/data/import", content=archive)
    me = (await b.get("/api/v1/auth/me")).json()
    async with db.get_sessionmaker()() as s:
        row = await s.scalar(select(Contact).where(Contact.user_id == uuid.UUID(me["id"])))
        assert row is not None and row.email_enc and "sam@example.com" not in row.email_enc
        profile = await s.get(VaultProfile, uuid.UUID(me["id"]))
        assert profile is not None and profile.email_enc and "@" not in profile.email_enc


async def test_import_size_limit(
    app_factory: Callable[[Settings], httpx.AsyncClient], make_settings: Callable[..., Settings]
) -> None:
    s = make_settings(import_archive_max_bytes=1000)
    c = app_factory(s)
    await c.get("/api/v1/auth/status")
    await c.post(
        "/api/v1/auth/register",
        json={"email": "jordan@example.com", "password": TEST_PASSWORD, "display_name": "J"},
    )
    r = await c.post("/api/v1/data/import", content=b"x" * 5000)
    assert r.status_code == 413


@pytest.mark.parametrize(
    ("tables", "message"),
    [
        (
            {
                "applications": [
                    {"id": str(uuid.uuid4()), "title": "x", "job_url": "javascript:alert(1)"}
                ]
            },
            "job_url must be a web address",
        ),
        (
            {
                "contacts": [
                    {"id": str(uuid.uuid4()), "name": "x", "linkedin_url": "data:text/html,x"}
                ]
            },
            "linkedin_url must be a web address",
        ),
        (
            {
                "vault_entries": [
                    {
                        "id": str(uuid.uuid4()),
                        "kind": "link",
                        "data": {"label": "x", "url": "javascript:x"},
                    }
                ]
            },
            "isn't valid for a link entry",
        ),
        (
            {"vault_entries": [{"id": str(uuid.uuid4()), "kind": "admin", "data": {}}]},
            "isn't a vault entry type",
        ),
    ],
)
async def test_import_applies_api_validation(
    user_client: httpx.AsyncClient, tables: dict[str, list[dict[str, Any]]], message: str
) -> None:
    r = await user_client.post("/api/v1/data/import", content=_archive(tables))
    assert r.status_code == 422, r.text
    assert message in r.json()["error"]["message"]


async def test_import_never_carries_pending_actions(
    two_users: tuple[httpx.AsyncClient, httpx.AsyncClient],
) -> None:
    _, b = two_users
    conv = str(uuid.uuid4())
    archive = _archive(
        {
            "conversations": [{"id": conv, "title": "Planted"}],
            "pending_actions": [
                {
                    "id": str(uuid.uuid4()),
                    "conversation_id": conv,
                    "tool": "move_application",
                    "tool_call_id": "call_1",
                    "arguments": {},
                    "summary": "Move everything to Rejected",
                    "status": "pending",
                }
            ],
        }
    )
    r = await b.post("/api/v1/data/import", content=archive)
    assert r.status_code == 200, r.text
    convs = (await b.get("/api/v1/assistant/conversations")).json()
    detail = (await b.get(f"/api/v1/assistant/conversations/{convs[0]['id']}")).json()
    assert detail["pending"] == 0


async def test_import_rejects_oversized_unpacked_archives(
    user_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from careerline.data import portable

    monkeypatch.setattr(portable, "MAX_TOTAL_BYTES", 100)
    r = await user_client.post("/api/v1/data/import", content=_archive({"applications": []}))
    assert r.status_code == 422 and "too large once unpacked" in r.json()["error"]["message"]
