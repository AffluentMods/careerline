# SPDX-License-Identifier: Apache-2.0
"""Vault API tests. All people, employers, and contact details here are fictional."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from careerline import db as dbmod
from careerline.models import AuditLog, VaultProfile

V = "/api/v1/vault"
ROLE = {"title": "Security Analyst", "employer": "Northwind Tools", "start": "2021-03"}


async def _create(c: httpx.AsyncClient, kind: str, data: dict[str, Any], **extra: Any) -> Any:
    r = await c.post(f"{V}/entries", json={"kind": kind, "data": data, **extra})
    assert r.status_code == 201, r.text
    return r.json()


async def _role_with_bullet(c: httpx.AsyncClient) -> tuple[Any, Any]:
    role = await _create(c, "role", ROLE)
    ach = await _create(
        c, "achievement", {"text": "Cut phishing clicks by 40%"}, parent_id=role["id"]
    )
    return role, ach


# --- auth & CSRF ----------------------------------------------------------------------


async def test_vault_requires_sign_in(client: httpx.AsyncClient) -> None:
    for path in ("/profile", "/entries", "/summary", "/stories"):
        r = await client.get(V + path)
        assert r.status_code == 401, path


async def test_vault_writes_require_csrf(user_client: httpx.AsyncClient) -> None:
    r = await user_client.post(
        f"{V}/entries",
        json={"kind": "skill", "data": {"name": "Go"}},
        headers={"x-csrf-token": "wrong"},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "csrf_failed"


# --- entries ----------------------------------------------------------------------------


async def test_entry_crud(user_client: httpx.AsyncClient) -> None:
    c = user_client
    role = await _create(c, "role", ROLE, evidence_note="Offer letter, 2021")
    assert role["verified"] is True and role["usable"] is True
    assert role["source"] == "manual" and role["verified_at"]
    assert role["data"]["end"] is None

    r = await c.get(f"{V}/entries/{role['id']}")
    assert r.json()["evidence_note"] == "Offer letter, 2021"

    r = await c.patch(f"{V}/entries/{role['id']}", json={"data": {**ROLE, "end": "2024-01"}})
    assert r.status_code == 200
    assert r.json()["data"]["end"] == "2024-01"
    assert r.json()["verified"] is True
    assert r.json()["verified_at"] >= role["verified_at"]

    r = await c.delete(f"{V}/entries/{role['id']}")
    assert r.status_code == 204
    assert (await c.get(f"{V}/entries/{role['id']}")).status_code == 404


async def test_create_validates_data(user_client: httpx.AsyncClient) -> None:
    r = await user_client.post(
        f"{V}/entries", json={"kind": "role", "data": {"title": "", "secret_field": "x"}}
    )
    assert r.status_code == 422
    body = r.json()["error"]
    assert body["code"] == "invalid_request"
    assert all(f["loc"][:2] == ["body", "data"] for f in body["fields"])
    r = await user_client.post(f"{V}/entries", json={"kind": "wizard", "data": {}})
    assert r.status_code == 422


async def test_parent_rules(user_client: httpx.AsyncClient) -> None:
    c = user_client
    r = await c.post(f"{V}/entries", json={"kind": "responsibility", "data": {"text": "x"}})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "parent_required"

    skill = await _create(c, "skill", {"name": "Python"})
    r = await c.post(
        f"{V}/entries",
        json={"kind": "achievement", "data": {"text": "x"}, "parent_id": skill["id"]},
    )
    assert r.json()["error"]["code"] == "invalid_parent"

    r = await c.post(
        f"{V}/entries",
        json={"kind": "achievement", "data": {"text": "x"}, "parent_id": str(uuid.uuid4())},
    )
    assert r.status_code == 404

    r = await c.patch(f"{V}/entries/{skill['id']}", json={"kind": "role"})
    assert r.json()["error"]["code"] == "data_required"


async def test_deleting_parent_removes_children(user_client: httpx.AsyncClient) -> None:
    role, ach = await _role_with_bullet(user_client)
    await user_client.delete(f"{V}/entries/{role['id']}")
    assert (await user_client.get(f"{V}/entries/{ach['id']}")).status_code == 404


async def test_kind_change_blocked_with_children(user_client: httpx.AsyncClient) -> None:
    role, _ = await _role_with_bullet(user_client)
    r = await user_client.patch(
        f"{V}/entries/{role['id']}", json={"kind": "project", "data": {"name": "x"}}
    )
    assert r.status_code == 409


async def test_kind_change_for_a_draft(user_client: httpx.AsyncClient) -> None:
    ach = await _create(user_client, "achievement", {"text": "Python, Go"}, verified=False)
    r = await user_client.patch(
        f"{V}/entries/{ach['id']}", json={"kind": "skill", "data": {"name": "Python"}}
    )
    assert r.status_code == 200
    assert r.json()["kind"] == "skill"
    assert r.json()["verified"] is False


async def test_usable_requires_verified_ancestors(user_client: httpx.AsyncClient) -> None:
    c = user_client
    role = await _create(c, "role", ROLE, verified=False)
    ach = await _create(c, "achievement", {"text": "Did a thing"}, parent_id=role["id"])
    assert ach["verified"] is True
    assert ach["usable"] is False
    await c.post(f"{V}/entries/verify", json={"ids": [role["id"]], "verified": True})
    assert (await c.get(f"{V}/entries/{ach['id']}")).json()["usable"] is True


async def test_list_filters_and_search(user_client: httpx.AsyncClient) -> None:
    c = user_client
    await _role_with_bullet(c)
    await _create(c, "skill", {"name": "Kusto", "category": "Query languages"}, verified=False)
    assert len((await c.get(f"{V}/entries")).json()) == 3
    assert [e["kind"] for e in (await c.get(f"{V}/entries?kind=skill")).json()] == ["skill"]
    assert len((await c.get(f"{V}/entries?verified=false")).json()) == 1
    hits = (await c.get(f"{V}/entries", params={"q": "PHISHING"})).json()
    assert [h["kind"] for h in hits] == ["achievement"]
    # Field names are not searched, only values.
    assert (await c.get(f"{V}/entries", params={"q": "title"})).json() == []
    assert (await c.get(f"{V}/entries", params={"q": "_"})).json() == []


async def test_bulk_verify_and_summary(user_client: httpx.AsyncClient) -> None:
    c = user_client
    a = await _create(c, "skill", {"name": "Go"}, verified=False)
    b = await _create(c, "skill", {"name": "Rust"}, verified=False)
    await _create(c, "skill", {"name": "C"})
    s = (await c.get(f"{V}/summary")).json()
    assert s["kinds"]["skill"] == {"total": 3, "verified": 1}
    assert s["needs_review"] == 2

    r = await c.post(f"{V}/entries/verify", json={"ids": [a["id"], b["id"]], "verified": True})
    assert r.json() == {"updated": 2}
    r = await c.post(f"{V}/entries/verify", json={"ids": [a["id"]], "verified": True})
    assert r.json() == {"updated": 0}
    assert (await c.get(f"{V}/summary")).json()["needs_review"] == 0

    async for s_ in dbmod.get_db():
        events = (await s_.scalars(select(AuditLog).where(AuditLog.event == "vault.verify"))).all()
    assert events[0].details == {"verified": True, "requested": 2, "count": 2}


# --- profile ------------------------------------------------------------------------------


async def test_profile_roundtrip_and_encryption(user_client: httpx.AsyncClient) -> None:
    c = user_client
    assert (await c.get(f"{V}/profile")).json()["full_name"] == ""
    body = {
        "full_name": "Jordan Rivera",
        "headline": "Security engineer",
        "location_text": "Springfield, ST",
        "summary": "Blue team generalist.",
        "email": "jordan@example.com",
        "phone": "555-0100",
        "address": "1 Example Way",
    }
    r = await c.put(f"{V}/profile", json=body)
    assert r.status_code == 200
    assert (await c.get(f"{V}/profile")).json() == body

    async for s in dbmod.get_db():
        row = await s.scalar(select(VaultProfile))
    assert row is not None
    for enc in (row.email_enc, row.phone_enc, row.address_enc):
        assert enc and enc.startswith("v1:")
    assert "555-0100" not in (row.phone_enc or "")

    r = await c.put(f"{V}/profile", json={**body, "phone": ""})
    assert (await c.get(f"{V}/profile")).json()["phone"] == ""


async def test_profile_rejects_bad_email(user_client: httpx.AsyncClient) -> None:
    r = await user_client.put(f"{V}/profile", json={"email": "nope"})
    assert r.status_code == 422


# --- stories ------------------------------------------------------------------------------


async def test_story_crud_and_links(user_client: httpx.AsyncClient) -> None:
    c = user_client
    role, ach = await _role_with_bullet(c)
    r = await c.post(
        f"{V}/stories",
        json={
            "title": "Phishing program",
            "situation": "Click rates were high.",
            "action": "Ran monthly simulations.",
            "result": "Clicks fell 40%.",
            "tags": ["leadership", "awareness"],
            "entry_ids": [ach["id"], ach["id"], role["id"]],
        },
    )
    assert r.status_code == 201, r.text
    story = r.json()
    assert sorted(story["entry_ids"]) == sorted([ach["id"], role["id"]])
    assert story["verified"] is True

    r = await c.patch(f"{V}/stories/{story['id']}", json={"entry_ids": [role["id"]]})
    assert r.json()["entry_ids"] == [role["id"]]
    assert r.json()["title"] == "Phishing program"

    hits = (await c.get(f"{V}/stories", params={"q": "awareness"})).json()
    assert [h["id"] for h in hits] == [story["id"]]

    # Deleting a linked entry drops the link but keeps the story.
    await c.delete(f"{V}/entries/{role['id']}")
    assert (await c.get(f"{V}/stories/{story['id']}")).json()["entry_ids"] == []

    assert (await c.delete(f"{V}/stories/{story['id']}")).status_code == 204
    assert (await c.get(f"{V}/stories/{story['id']}")).status_code == 404


async def test_story_rejects_unknown_entry(user_client: httpx.AsyncClient) -> None:
    r = await user_client.post(
        f"{V}/stories", json={"title": "x", "entry_ids": [str(uuid.uuid4())]}
    )
    assert r.status_code == 404
    assert (await user_client.get(f"{V}/stories")).json() == []


# --- isolation -------------------------------------------------------------------------


async def test_cross_user_isolation(
    two_users: tuple[httpx.AsyncClient, httpx.AsyncClient],
) -> None:
    a, b = two_users
    role, ach = await _role_with_bullet(a)
    story = (await a.post(f"{V}/stories", json={"title": "A's story"})).json()
    await a.put(f"{V}/profile", json={"full_name": "Jordan Rivera", "phone": "555-0100"})

    # B sees none of A's data.
    assert (await b.get(f"{V}/entries")).json() == []
    assert (await b.get(f"{V}/stories")).json() == []
    assert (await b.get(f"{V}/profile")).json()["phone"] == ""
    assert (await b.get(f"{V}/summary")).json()["needs_review"] == 0

    # Every per-id route answers 404, as if the row didn't exist.
    eid, sid = role["id"], story["id"]
    attempts = [
        b.get(f"{V}/entries/{eid}"),
        b.patch(f"{V}/entries/{eid}", json={"evidence_note": "hijack"}),
        b.delete(f"{V}/entries/{eid}"),
        b.get(f"{V}/stories/{sid}"),
        b.patch(f"{V}/stories/{sid}", json={"title": "hijack"}),
        b.delete(f"{V}/stories/{sid}"),
        b.post(f"{V}/stories", json={"title": "x", "entry_ids": [eid]}),
        b.post(
            f"{V}/entries",
            json={"kind": "achievement", "data": {"text": "x"}, "parent_id": eid},
        ),
    ]
    for coro in attempts:
        r = await coro
        assert r.status_code == 404, r.request.url

    # Bulk verify silently skips rows that aren't B's.
    await a.post(f"{V}/entries/verify", json={"ids": [eid], "verified": False})
    r = await b.post(f"{V}/entries/verify", json={"ids": [eid], "verified": True})
    assert r.json() == {"updated": 0}

    # B can't move its own entry under A's role either.
    mine = await _create(b, "achievement", {"text": "mine"})
    r = await b.patch(f"{V}/entries/{mine['id']}", json={"parent_id": eid})
    assert r.status_code == 404

    # A's data is unchanged.
    got = (await a.get(f"{V}/entries/{eid}")).json()
    assert got["evidence_note"] == "" and got["verified"] is False
    assert (await a.get(f"{V}/stories/{sid}")).json()["title"] == "A's story"
    assert (await a.get(f"{V}/entries/{ach['id']}")).status_code == 200


async def test_database_blocks_cross_user_links(
    two_users: tuple[httpx.AsyncClient, httpx.AsyncClient],
) -> None:
    """Even if app code had a bug, composite foreign keys reject cross-user references."""
    a, b = two_users
    role, _ = await _role_with_bullet(a)
    b_story = (await b.post(f"{V}/stories", json={"title": "B"})).json()
    async for s in dbmod.get_db():
        b_user = await s.scalar(text("SELECT id FROM users WHERE email = 'casey@example.com'"))
        with pytest.raises(IntegrityError):
            await s.execute(
                text("INSERT INTO story_entries (story_id, entry_id, user_id) VALUES (:s, :e, :u)"),
                {"s": b_story["id"], "e": role["id"], "u": b_user},
            )
        await s.rollback()
        with pytest.raises(IntegrityError):
            await s.execute(
                text(
                    "INSERT INTO vault_entries (id, user_id, kind, parent_id, data, "
                    "evidence_note, verified, source) VALUES (gen_random_uuid(), :u, "
                    "'achievement', :p, '{}', '', false, 'manual')"
                ),
                {"u": b_user, "p": role["id"]},
            )
        await s.rollback()
