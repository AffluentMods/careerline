# SPDX-License-Identifier: Apache-2.0
"""API keys with full access: direct changes, the REST API, and what stays out of reach."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import select, text

from careerline import db as dbmod
from careerline.models import AuditLog
from careerline.tracker.models import Reminder
from tests.conftest import TEST_PASSWORD
from tests.test_api_access import KEYS, _call, _data, _key, _mcp_client, _rpc

FULL = ["full"]
GREENHOUSE = {
    "kind": "greenhouse",
    "name": "Northwind (Greenhouse)",
    "config": {"board": "northwind", "company": "Northwind Tools"},
}


def _error(result: dict[str, Any]) -> str:
    assert result["isError"] is True, result
    return str(result["content"][0]["text"])


async def _api(
    mc: httpx.AsyncClient, key: str, method: str, path: str, **extra: Any
) -> dict[str, Any]:
    return await _call(mc, key, "api_request", {"method": method, "path": path, **extra})


async def test_full_access_tools_and_instructions(
    user_client: httpx.AsyncClient,
    app_factory: Callable[..., httpx.AsyncClient],
    settings: Any,
) -> None:
    key = await _key(user_client, FULL, name="Everything")
    mc = _mcp_client(app_factory, settings)

    init = (await _rpc(mc, key, "initialize", {"protocolVersion": "2025-06-18"})).json()
    assert "FULL ACCESS" in init["result"]["instructions"]
    tools = {t["name"]: t for t in (await _rpc(mc, key, "tools/list")).json()["result"]["tools"]}
    # Full includes every narrower tool, plus the REST API.
    assert {"get_overview", "add_vault_entry", "create_reminder", "api_request"} <= set(tools)
    assert tools["api_request"]["annotations"]["destructiveHint"] is True
    assert tools["create_reminder"]["title"] == "Add a reminder"
    assert "Applies immediately" in tools["create_reminder"]["description"]
    assert "confirm" not in tools["create_reminder"]["description"].lower()

    # A narrower key doesn't see the full-access tools.
    narrow = await _key(user_client, ["read", "draft", "propose"], name="Narrow")
    names = {t["name"] for t in (await _rpc(mc, narrow, "tools/list")).json()["result"]["tools"]}
    assert not names & {"api_request", "api_reference"}
    r = await _rpc(mc, narrow, "tools/call", {"name": "api_request", "arguments": {}})
    assert r.json()["error"]["code"] == -32602


async def test_full_access_saves_verified_and_applies_changes(
    user_client: httpx.AsyncClient,
    app_factory: Callable[..., httpx.AsyncClient],
    settings: Any,
) -> None:
    app = (
        await user_client.post(
            "/api/v1/tracker/applications",
            json={"title": "Security Engineer", "company": "Northwind Tools", "stage": "applied"},
        )
    ).json()
    key = await _key(user_client, FULL)
    mc = _mcp_client(app_factory, settings)

    role = _data(
        await _call(
            mc,
            key,
            "add_vault_entry",
            {"kind": "role", "data": {"title": "Data Analyst", "employer": "Northwind"}},
        )
    )
    assert role["status"] == "verified"
    story = _data(await _call(mc, key, "add_story", {"title": "Faster reports"}))
    assert story["status"] == "verified"
    entries = (await user_client.get("/api/v1/vault/entries")).json()
    assert entries[0]["verified"] and entries[0]["usable"] and entries[0]["source"] == "api"
    assert (await user_client.get("/api/v1/vault/stories")).json()[0]["verified"] is True

    done = _data(
        await _call(
            mc,
            key,
            "create_reminder",
            {
                "title": "Follow up",
                "due_on": "2026-11-12",
                "kind": "follow_up",
                "application_id": app["id"],
            },
        )
    )
    assert done["status"] == "done" and done["result"] == "Reminder added."
    moved = _data(
        await _call(
            mc,
            key,
            "move_application",
            {"application_id": app["id"], "stage": "interviewing", "note": ""},
        )
    )
    assert moved["status"] == "done"
    async for db in dbmod.get_db():
        assert [r.title for r in (await db.scalars(select(Reminder))).all()] == ["Follow up"]
    got = (await user_client.get(f"/api/v1/tracker/applications/{app['id']}")).json()
    assert got["application"]["stage"] == "interviewing"
    # Nothing waits for confirmation, and no proposals conversation is created.
    assert _data(await _call(mc, key, "list_my_proposals", {})) == []
    assert (await user_client.get("/api/v1/assistant/conversations")).json() == []
    # Invalid changes still fail cleanly.
    assert "Nothing to change" in _error(await _call(mc, key, "update_profile", {}))


async def test_api_reference(
    user_client: httpx.AsyncClient,
    app_factory: Callable[..., httpx.AsyncClient],
    settings: Any,
) -> None:
    key = await _key(user_client, FULL)
    mc = _mcp_client(app_factory, settings)

    listing = _data(await _call(mc, key, "api_reference", {}))["endpoints"]
    assert any(e.startswith("POST /api/v1/discovery/sources ") for e in listing)
    assert any(e.startswith("DELETE /api/v1/vault/entries/{entry_id} ") for e in listing)
    # What keys can't reach isn't advertised.
    assert not any(
        p in e
        for e in listing
        for p in ("/api/v1/auth", "/api/v1/api-tokens", "/data/export", "/data/import")
    )

    detail = _data(
        await _call(mc, key, "api_reference", {"path_prefix": "/api/v1/tracker/contacts"})
    )
    create = next(d for d in detail if d["method"] == "POST" and d["path"].endswith("/contacts"))
    schema = create["body"]["application/json"]
    assert "name" in schema["properties"] and "$ref" not in json.dumps(schema)

    assert "No endpoints" in _error(await _call(mc, key, "api_reference", {"path_prefix": "/x"}))
    assert "longer path_prefix" in _error(
        await _call(mc, key, "api_reference", {"path_prefix": "/api/v1/"})
    )


async def test_api_request_reaches_everything(
    user_client: httpx.AsyncClient,
    app_factory: Callable[..., httpx.AsyncClient],
    settings: Any,
) -> None:
    key = await _key(user_client, FULL)
    mc = _mcp_client(app_factory, settings)

    # Job sources.
    source = _data(await _api(mc, key, "POST", "/api/v1/discovery/sources", body=GREENHOUSE))
    assert source["status"] == 201 and source["body"]["kind"] == "greenhouse"
    sid = source["body"]["id"]
    renamed = _data(
        await _api(mc, key, "PATCH", f"/api/v1/discovery/sources/{sid}", body={"name": "NW"})
    )
    assert renamed["body"]["name"] == "NW"
    assert [s["name"] for s in (await user_client.get("/api/v1/discovery/sources")).json()] == [
        "NW"
    ]

    # Contacts, with their details (full access sees contact details).
    contact = _data(
        await _api(
            mc,
            key,
            "POST",
            "/api/v1/tracker/contacts",
            body={"name": "Sam Ortiz", "email": "sam@example.com"},
        )
    )["body"]
    listed = _data(await _api(mc, key, "GET", "/api/v1/tracker/contacts", query={"q": "Sam"}))
    assert listed["body"][0]["email"] == "sam@example.com"

    # Edit and delete vault entries.
    entry = (
        await user_client.post(
            "/api/v1/vault/entries", json={"kind": "skill", "data": {"name": "SQL"}}
        )
    ).json()
    _data(
        await _api(
            mc,
            key,
            "PATCH",
            f"/api/v1/vault/entries/{entry['id']}",
            body={"data": {"name": "PostgreSQL"}},
        )
    )
    assert (await user_client.get(f"/api/v1/vault/entries/{entry['id']}")).json()["data"][
        "name"
    ] == "PostgreSQL"
    deleted = _data(await _api(mc, key, "DELETE", f"/api/v1/vault/entries/{entry['id']}"))
    assert deleted == {"status": 204, "body": None}
    _data(await _api(mc, key, "DELETE", f"/api/v1/tracker/contacts/{contact['id']}"))
    _data(await _api(mc, key, "DELETE", f"/api/v1/discovery/sources/{sid}"))
    assert (await user_client.get("/api/v1/vault/entries")).json() == []
    assert (await user_client.get("/api/v1/discovery/sources")).json() == []

    # Errors come back as tool errors with the API's message.
    assert "HTTP 404" in _error(await _api(mc, key, "GET", f"/api/v1/vault/entries/{sid}"))
    assert "HTTP 422" in _error(
        await _api(mc, key, "POST", "/api/v1/tracker/contacts", body={"nope": 1})
    )
    assert "/api/v1/" in _error(await _api(mc, key, "GET", "/api/mcp"))
    assert "/api/v1/" in _error(await _api(mc, key, "GET", "/api/v1/vault/../../healthz"))

    async for db in dbmod.get_db():
        rows = (await db.scalars(select(AuditLog).where(AuditLog.event == "api.request"))).all()
    assert {(r.details["method"], r.details["path"].split("/")[3]) for r in rows} >= {
        ("POST", "discovery"),
        ("DELETE", "vault"),
    }
    assert all(r.details.get("via_api_key") for r in rows)


async def test_keys_never_reach_sign_in_security_keys_or_export(
    user_client: httpx.AsyncClient,
    app_factory: Callable[..., httpx.AsyncClient],
    settings: Any,
) -> None:
    key = await _key(user_client, FULL)
    mc = _mcp_client(app_factory, settings)
    for method, path, body in (
        ("POST", "/api/v1/auth/password", {"current": TEST_PASSWORD, "new": "x" * 16}),
        ("GET", "/api/v1/auth/sessions", None),
        ("POST", "/api/v1/auth/mfa/disable", {}),
        ("POST", "/api/v1/api-tokens", {"name": "more", "scopes": ["full"]}),
        ("GET", "/api/v1/api-tokens", None),
        ("POST", "/api/v1/data/export", {}),
    ):
        msg = _error(await _api(mc, key, method, path, body=body))
        assert "can't change sign-in security" in msg, (path, msg)
        # And not over plain REST either: the key isn't accepted there at all.
        r = await mc.request(method, path, json=body, headers={"authorization": f"Bearer {key}"})
        assert r.status_code in (401, 403), (path, r.status_code)
    assert len((await user_client.get(KEYS)).json()) == 1


async def test_rest_api_with_a_key(
    user_client: httpx.AsyncClient,
    app_factory: Callable[..., httpx.AsyncClient],
    settings: Any,
) -> None:
    full = await _key(user_client, FULL, name="Full")
    narrow = await _key(user_client, ["read", "draft", "propose"], name="Narrow")
    c = _mcp_client(app_factory, settings)  # no cookies, no CSRF token
    auth = {"authorization": f"Bearer {full}"}

    r = await c.post(
        "/api/v1/vault/entries", json={"kind": "skill", "data": {"name": "Go"}}, headers=auth
    )
    assert r.status_code == 201, r.text
    assert (await c.get("/api/v1/vault/entries", headers=auth)).json()[0]["data"]["name"] == "Go"

    r = await c.get("/api/v1/vault/entries", headers={"authorization": f"Bearer {narrow}"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "full_access_required"
    r = await c.get("/api/v1/vault/entries", headers={"authorization": "Bearer cl_" + "x" * 43})
    assert r.status_code == 401
    # Without a key or a session, still refused (and CSRF still applies to sessions).
    assert (await c.post("/api/v1/vault/entries", json={})).status_code in (401, 403)


async def test_upgrading_an_existing_key(
    user_client: httpx.AsyncClient,
    app_factory: Callable[..., httpx.AsyncClient],
    settings: Any,
) -> None:
    key = await _key(user_client, ["read", "draft", "propose"])
    mc = _mcp_client(app_factory, settings)
    key_id = (await user_client.get(KEYS)).json()[0]["id"]
    names = {t["name"] for t in (await _rpc(mc, key, "tools/list")).json()["result"]["tools"]}
    assert "api_request" not in names

    # Changing access needs the password again, like creating a key.
    async for db in dbmod.get_db():
        await db.execute(text("UPDATE auth_sessions SET reauth_at = NULL"))
        await db.commit()
    r = await user_client.patch(f"{KEYS}/{key_id}", json={"scopes": ["full"]})
    assert r.status_code == 403 and r.json()["error"]["code"] == "reauth_required"
    assert (
        await user_client.post("/api/v1/auth/reauth", json={"password": TEST_PASSWORD})
    ).is_success

    assert (await user_client.patch(f"{KEYS}/{key_id}", json={"scopes": []})).status_code == 422
    r = await user_client.patch(f"{KEYS}/{key_id}", json={"scopes": ["full", "read"]})
    assert r.status_code == 200, r.text
    assert r.json()["scopes"] == ["read", "full"] and "token" not in r.json()

    # The same key now has full access.
    names = {t["name"] for t in (await _rpc(mc, key, "tools/list")).json()["result"]["tools"]}
    assert "api_request" in names
    assert (
        await user_client.patch(
            f"{KEYS}/00000000-0000-0000-0000-000000000000", json={"scopes": ["read"]}
        )
    ).status_code == 404
    async for db in dbmod.get_db():
        event = (
            await db.scalars(select(AuditLog).where(AuditLog.event == "api_token.updated"))
        ).one()
    assert event.details["scopes_before"] == ["read", "draft", "propose"]
