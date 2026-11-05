# SPDX-License-Identifier: Apache-2.0
"""API keys and the MCP endpoint."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select, text, update

from careerline import db as dbmod
from careerline.api_access.models import ApiToken
from careerline.config import Settings
from careerline.models import AuditLog
from careerline.tracker.models import Reminder
from tests.conftest import TEST_PASSWORD

KEYS = "/api/v1/api-tokens"
MCP = "/api/mcp"
ALL = ["read", "draft", "propose"]


async def _key(c: httpx.AsyncClient, scopes: list[str] = ALL, name: str = "Claude") -> str:
    r = await c.post(KEYS, json={"name": name, "scopes": scopes, "expires_in_days": 30})
    assert r.status_code == 201, r.text
    return str(r.json()["token"])


async def _rpc(
    c: httpx.AsyncClient, key: str | None, method: str, params: Any = None, msg_id: Any = 1
) -> httpx.Response:
    headers = {"authorization": f"Bearer {key}"} if key else {}
    msg: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        msg["params"] = params
    return await c.post(MCP, json=msg, headers=headers)


async def _call(c: httpx.AsyncClient, key: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    r = await _rpc(c, key, "tools/call", {"name": name, "arguments": args})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "result" in body, body
    result: dict[str, Any] = body["result"]
    return result


def _data(result: dict[str, Any]) -> Any:
    assert result["isError"] is False, result["content"][0]["text"]
    return json.loads(result["content"][0]["text"])


def _mcp_client(
    app_factory: Callable[[Settings], httpx.AsyncClient], settings: Settings
) -> httpx.AsyncClient:
    """A separate client with no cookies: MCP clients authenticate with the key alone."""
    return app_factory(settings)


# --- key management --------------------------------------------------------------------------


async def test_key_lifecycle(
    user_client: httpx.AsyncClient,
    app_factory: Callable[[Settings], httpx.AsyncClient],
    settings: Settings,
) -> None:
    r = await user_client.post(KEYS, json={"name": "Claude", "scopes": ["propose", "read"]})
    assert r.status_code == 201, r.text
    created = r.json()
    raw = created["token"]
    assert raw.startswith("cl_") and len(raw) > 40
    assert created["prefix"] == raw[:8] and created["scopes"] == ["read", "propose"]
    assert created["expires_at"] is not None  # 90 days by default

    listed = (await user_client.get(KEYS)).json()
    assert [k["name"] for k in listed] == ["Claude"] and "token" not in listed[0]

    async for db in dbmod.get_db():
        stored = (await db.scalars(select(ApiToken))).one()
        assert raw.encode() not in stored.token_hash and len(stored.token_hash) == 32

    mc = _mcp_client(app_factory, settings)
    assert (await _rpc(mc, raw, "ping")).json()["result"] == {}
    async for db in dbmod.get_db():
        assert (await db.scalars(select(ApiToken))).one().last_used_at is not None

    assert (await user_client.delete(f"{KEYS}/{created['id']}")).status_code == 204
    assert (await _rpc(mc, raw, "ping")).status_code == 401
    assert (await user_client.delete(f"{KEYS}/{created['id']}")).status_code == 404

    async for db in dbmod.get_db():
        events = (
            await db.scalars(select(AuditLog.event).where(AuditLog.event.like("api_token%")))
        ).all()
        details = json.dumps([a.details for a in (await db.scalars(select(AuditLog))).all()])
    assert sorted(events) == ["api_token.created", "api_token.revoked"]
    assert raw not in details  # the key itself is never logged


async def test_creating_a_key_needs_a_recent_password(user_client: httpx.AsyncClient) -> None:
    async for db in dbmod.get_db():
        await db.execute(text("UPDATE auth_sessions SET reauth_at = NULL"))
        await db.commit()
    r = await user_client.post(KEYS, json={"name": "x", "scopes": ["read"]})
    assert r.status_code == 403 and r.json()["error"]["code"] == "reauth_required"
    assert (
        await user_client.post("/api/v1/auth/reauth", json={"password": TEST_PASSWORD})
    ).is_success
    assert (await user_client.post(KEYS, json={"name": "x", "scopes": ["read"]})).status_code == 201


async def test_key_input_is_validated(user_client: httpx.AsyncClient) -> None:
    for body in (
        {"name": "x", "scopes": []},
        {"name": "x", "scopes": ["admin"]},
        {"name": "", "scopes": ["read"]},
        {"name": "x", "scopes": ["read"], "expires_in_days": 5},
    ):
        assert (await user_client.post(KEYS, json=body)).status_code == 422, body
    r = await user_client.post(
        KEYS, json={"name": "x", "scopes": ["read", "read"], "expires_in_days": None}
    )
    assert r.json()["scopes"] == ["read"] and r.json()["expires_at"] is None


# --- authentication and protocol ------------------------------------------------------------------


async def test_mcp_requires_a_valid_key(
    user_client: httpx.AsyncClient,
    app_factory: Callable[[Settings], httpx.AsyncClient],
    settings: Settings,
) -> None:
    key = await _key(user_client)
    mc = _mcp_client(app_factory, settings)

    r = await _rpc(mc, None, "ping")
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Bearer")
    assert (await _rpc(mc, "cl_" + "x" * 43, "ping")).status_code == 401
    assert (await _rpc(mc, "not-a-key", "ping")).status_code == 401
    # A signed-in browser session is not enough: the endpoint only accepts keys.
    assert (await _rpc(user_client, None, "ping")).status_code == 401

    async for db in dbmod.get_db():
        await db.execute(
            update(ApiToken).values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await db.commit()
    assert (await _rpc(mc, key, "ping")).status_code == 401
    assert (await user_client.get(KEYS)).json()[0]["expired"] is True


async def test_mcp_refuses_foreign_origins_and_other_methods(
    user_client: httpx.AsyncClient,
    app_factory: Callable[[Settings], httpx.AsyncClient],
    settings: Settings,
) -> None:
    key = await _key(user_client)
    mc = _mcp_client(app_factory, settings)
    msg = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    auth = {"authorization": f"Bearer {key}"}
    r = await mc.post(MCP, json=msg, headers={**auth, "origin": "https://evil.example"})
    assert r.status_code == 403
    r = await mc.post(MCP, json=msg, headers={**auth, "origin": "https://testserver"})
    assert r.status_code == 200
    assert (await mc.get(MCP, headers=auth)).status_code == 405


async def test_mcp_protocol(
    user_client: httpx.AsyncClient,
    app_factory: Callable[[Settings], httpx.AsyncClient],
    settings: Settings,
) -> None:
    key = await _key(user_client)
    mc = _mcp_client(app_factory, settings)

    init = (
        await _rpc(mc, key, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
    ).json()
    assert init["id"] == 1 and init["result"]["protocolVersion"] == "2025-06-18"
    assert init["result"]["serverInfo"]["name"] == "careerline"
    assert (
        "tools" in init["result"]["capabilities"] and "verified" in init["result"]["instructions"]
    )
    newest = (await _rpc(mc, key, "initialize", {"protocolVersion": "1999-01-01"})).json()
    assert newest["result"]["protocolVersion"] == "2025-11-25"

    note = await mc.post(
        MCP,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"authorization": f"Bearer {key}"},
    )
    assert note.status_code == 202 and note.content == b""

    assert (await _rpc(mc, key, "nope/x")).json()["error"]["code"] == -32601
    batch = await mc.post(
        MCP,
        json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"}],
        headers={"authorization": f"Bearer {key}"},
    )
    assert batch.status_code == 400 and batch.json()["error"]["code"] == -32600
    bad = await mc.post(
        MCP,
        content=b"{nope",
        headers={"authorization": f"Bearer {key}", "content-type": "application/json"},
    )
    assert bad.status_code == 400 and bad.json()["error"]["code"] == -32700

    tools = (await _rpc(mc, key, "tools/list")).json()["result"]["tools"]
    by_name = {t["name"]: t for t in tools}
    assert {
        "get_overview",
        "add_vault_entry",
        "import_job",
        "create_reminder",
        "update_profile",
    } <= set(by_name)
    assert by_name["get_overview"]["annotations"]["readOnlyHint"] is True
    assert by_name["add_vault_entry"]["inputSchema"]["type"] == "object"
    assert all(t["annotations"]["destructiveHint"] is False for t in tools)
    # Nothing that reaches the account itself, deletes, or sends anything.
    assert not any(w in n for n in by_name for w in ("delete", "password", "export", "send", "key"))

    overview = _data(await _call(mc, key, "get_overview", {}))
    assert overview["user"] == "Jordan Rivera" and overview["key"]["scopes"] == ALL


async def test_scopes_limit_the_tools(
    user_client: httpx.AsyncClient,
    app_factory: Callable[[Settings], httpx.AsyncClient],
    settings: Settings,
) -> None:
    key = await _key(user_client, ["read"], name="Reader")
    mc = _mcp_client(app_factory, settings)
    names = {t["name"] for t in (await _rpc(mc, key, "tools/list")).json()["result"]["tools"]}
    assert "list_vault_entries" in names and "search_jobs" in names
    assert not names & {"add_vault_entry", "import_job", "create_reminder", "update_profile"}
    r = await _rpc(mc, key, "tools/call", {"name": "add_vault_entry", "arguments": {}})
    assert r.json()["error"]["code"] == -32602

    drafts_only = await _key(user_client, ["draft"], name="Drafter")
    names = {
        t["name"] for t in (await _rpc(mc, drafts_only, "tools/list")).json()["result"]["tools"]
    }
    assert names == {"add_vault_entry", "add_story", "import_job"}


# --- tools ------------------------------------------------------------------------------------------


async def test_vault_facts_arrive_as_drafts(
    user_client: httpx.AsyncClient,
    app_factory: Callable[[Settings], httpx.AsyncClient],
    settings: Settings,
) -> None:
    key = await _key(user_client)
    mc = _mcp_client(app_factory, settings)

    role = _data(
        await _call(
            mc,
            key,
            "add_vault_entry",
            {
                "kind": "role",
                "data": {"title": "Data Analyst", "employer": "Northwind", "start": "2022-03"},
                "evidence_note": "From the user's resume",
            },
        )
    )
    assert role["status"] == "draft"
    win = _data(
        await _call(
            mc,
            key,
            "add_vault_entry",
            {
                "kind": "achievement",
                "parent_id": role["id"],
                "data": {"text": "Cut report time by 40%", "metric": {"value": "40", "unit": "%"}},
            },
        )
    )
    skill = _data(
        await _call(mc, key, "add_vault_entry", {"kind": "skill", "data": {"name": "SQL"}})
    )

    bad_parent = await _call(
        mc,
        key,
        "add_vault_entry",
        {"kind": "responsibility", "parent_id": skill["id"], "data": {"text": "x"}},
    )
    assert (
        bad_parent["isError"]
        and "Allowed parents for responsibility: role" in bad_parent["content"][0]["text"]
    )
    bad_data = await _call(mc, key, "add_vault_entry", {"kind": "role", "data": {"employer": "x"}})
    assert bad_data["isError"] and "title" in bad_data["content"][0]["text"]
    bad_kind = await _call(mc, key, "add_vault_entry", {"kind": "hobby", "data": {}})
    assert bad_kind["isError"]

    entries = (await user_client.get("/api/v1/vault/entries")).json()
    assert len(entries) == 3
    assert all(e["source"] == "api" and not e["verified"] and not e["usable"] for e in entries)
    assert next(e for e in entries if e["id"] == win["id"])["parent_id"] == role["id"]

    # Drafts are never handed out as facts to write with.
    assert _data(await _call(mc, key, "get_vault_facts", {}))["verified_facts"] == []
    listed = _data(await _call(mc, key, "list_vault_entries", {"which": "drafts"}))
    assert len(listed) == 3

    story = _data(
        await _call(
            mc,
            key,
            "add_story",
            {
                "title": "Faster reports",
                "situation": "Slow reports",
                "result": "40% faster",
                "entry_ids": [win["id"]],
            },
        )
    )
    stories = (await user_client.get("/api/v1/vault/stories")).json()
    assert stories[0]["id"] == story["id"] and not stories[0]["verified"]
    assert stories[0]["entry_ids"] == [win["id"]]


async def test_changes_are_proposals_until_confirmed(
    user_client: httpx.AsyncClient,
    app_factory: Callable[[Settings], httpx.AsyncClient],
    settings: Settings,
) -> None:
    app = (
        await user_client.post(
            "/api/v1/tracker/applications",
            json={"title": "Security Engineer", "company": "Northwind Tools", "stage": "applied"},
        )
    ).json()
    key = await _key(user_client)
    mc = _mcp_client(app_factory, settings)

    proposed = _data(
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
    assert proposed["status"] == "waiting_for_user_confirmation"
    profile = _data(
        await _call(
            mc,
            key,
            "update_profile",
            {"headline": "Security Engineer", "full_name": "Jordan Rivera"},
        )
    )
    assert "headline to “Security Engineer”" in profile["proposal"]
    empty = await _call(mc, key, "update_profile", {})
    assert empty["isError"]

    async for db in dbmod.get_db():
        assert (await db.scalars(select(Reminder))).all() == []

    convs = (await user_client.get("/api/v1/assistant/conversations")).json()
    assert [c["source"] for c in convs] == ["api"] and "Claude" in convs[0]["title"]
    detail = (await user_client.get(f"/api/v1/assistant/conversations/{convs[0]['id']}")).json()
    actions = [m for m in detail["messages"] if m["role"] == "action"]
    assert [a["status"] for a in actions] == ["pending", "pending"] and detail["pending"] == 2
    keys = (await user_client.get(KEYS)).json()
    assert keys[0]["pending_proposals"] == 2 and keys[0]["conversation_id"] == convs[0]["id"]

    # It can't be chatted in (its history didn't come from a model).
    r = await user_client.post(
        f"/api/v1/assistant/conversations/{convs[0]['id']}/messages", json={"text": "hi"}
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "api_conversation"

    for a in actions:
        assert (
            await user_client.post(f"/api/v1/assistant/actions/{a['id']}", json={"confirm": True})
        ).is_success
    reminders = (await user_client.get("/api/v1/tracker/reminders")).json()
    assert [x["title"] for x in reminders] == ["Follow up"]
    assert (await user_client.get("/api/v1/vault/profile")).json()[
        "headline"
    ] == "Security Engineer"

    decided = _data(await _call(mc, key, "list_my_proposals", {}))
    assert {p["status"] for p in decided} == {"confirmed"}
    assert _data(await _call(mc, key, "get_overview", {}))["pending_proposals_from_this_key"] == 0


async def test_import_job_and_read_it_back(
    user_client: httpx.AsyncClient,
    app_factory: Callable[[Settings], httpx.AsyncClient],
    settings: Settings,
) -> None:
    key = await _key(user_client)
    mc = _mcp_client(app_factory, settings)
    missing_title = await _call(mc, key, "import_job", {"text": "Great job"})
    assert missing_title["isError"]
    job = _data(
        await _call(
            mc,
            key,
            "import_job",
            {
                "text": "Data Analyst, remote. $90,000 - $110,000 per year. SQL and Python.",
                "title": "Data Analyst",
                "company": "Contoso Freight",
                "location": "Remote",
            },
        )
    )
    got = _data(await _call(mc, key, "get_job", {"posting_id": job["id"]}))
    assert got["company"] == "Contoso Freight" and got["description"].startswith(
        "<untrusted_document"
    )
    found = _data(
        await _call(
            mc,
            key,
            "search_jobs",
            {
                "query": "analyst",
                "min_hourly_dollars": None,
                "location": None,
                "remote_only": False,
                "include_filtered": True,
            },
        )
    )
    assert [j["id"] for j in found] == [job["id"]]


async def test_keys_only_reach_their_own_account(
    two_users: tuple[httpx.AsyncClient, httpx.AsyncClient],
    app_factory: Callable[[Settings], httpx.AsyncClient],
    make_settings: Callable[..., Settings],
) -> None:
    a, b = two_users
    b_app = (
        await b.post(
            "/api/v1/tracker/applications", json={"title": "Secret role", "company": "B Co"}
        )
    ).json()
    await b.put(
        "/api/v1/vault/profile", json={"full_name": "Casey Lin", "email": "casey@example.com"}
    )
    await a.put(
        "/api/v1/vault/profile",
        json={"full_name": "Jordan Rivera", "email": "jordan@example.com", "phone": "555"},
    )
    key_a = await _key(a)
    mc = _mcp_client(app_factory, make_settings(allow_registration=True))

    other = await _call(mc, key_a, "get_application", {"application_id": b_app["id"]})
    assert other["isError"] and "No application" in other["content"][0]["text"]
    assert _data(await _call(mc, key_a, "list_applications", {"stage": None})) == []

    profile = _data(await _call(mc, key_a, "get_profile", {}))
    assert profile["full_name"] == "Jordan Rivera"
    assert "jordan@example.com" not in json.dumps(profile) and "555" not in json.dumps(profile)

    # B can't see or revoke A's keys.
    assert (await b.get(KEYS)).json() == []
    a_key_id = (await a.get(KEYS)).json()[0]["id"]
    assert (await b.delete(f"{KEYS}/{a_key_id}")).status_code == 404
