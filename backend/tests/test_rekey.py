# SPDX-License-Identifier: Apache-2.0
"""Key rotation: re-encrypt every encrypted column, then run with only the new key."""

from __future__ import annotations

import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pyotp
import pytest
from sqlalchemy import select

from tests.conftest import BACKEND, TEST_PASSWORD
from tests.resume_fixtures import make_pdf
from waypoint import db, model_registry
from waypoint.config import Settings
from waypoint.data import rekey
from waypoint.db import Base
from waypoint.models import User
from waypoint.security import contexts
from waypoint.security.crypto import FieldCipher, generate_key

_ = model_registry

OLD, NEW = generate_key(), generate_key()


def test_every_encrypted_column_is_covered() -> None:
    """A new *_enc column must be added to rekey.FIELDS, or rotation would skip it."""
    in_db = {
        f"{t.name}.{c.name}"
        for t in Base.metadata.sorted_tables
        for c in t.columns
        if c.name.endswith("_enc")
    }
    assert {f.name for f in rekey.FIELDS} == in_db


def test_version_of() -> None:
    c = FieldCipher(f"7:{OLD}")
    assert c.active_version == "7" and c.versions == ["7"]
    assert FieldCipher.version_of(c.encrypt("x", "ctx")) == "7"
    assert FieldCipher.version_of(c.encrypt_bytes(b"x", "ctx")) == "7"
    assert FieldCipher.version_of("garbage") == ""


async def _register(c: httpx.AsyncClient, email: str) -> None:
    await c.get("/api/v1/auth/status")
    r = await c.post(
        "/api/v1/auth/register",
        json={"email": email, "password": TEST_PASSWORD, "display_name": "Fictional Person"},
    )
    assert r.status_code == 201, r.text


async def _fill_every_encrypted_column(
    a: httpx.AsyncClient, b: httpx.AsyncClient
) -> dict[str, Any]:
    """Write each encrypted column through the app's own code paths."""
    out: dict[str, Any] = {}
    # users.mfa_secret_enc (a) and users.mfa_pending_secret_enc (b)
    r = await a.post("/api/v1/auth/mfa/setup", json={"password": TEST_PASSWORD})
    out["totp"] = r.json()["secret"]
    r = await a.post("/api/v1/auth/mfa/enable", json={"code": pyotp.TOTP(out["totp"]).now()})
    assert r.status_code == 200, r.text
    r = await b.post("/api/v1/auth/mfa/setup", json={"password": TEST_PASSWORD})
    out["pending"] = r.json()["secret"]
    # vault_profiles.*
    r = await a.put(
        "/api/v1/vault/profile",
        json={
            "full_name": "Jordan Rivera",
            "email": "jordan@example.com",
            "phone": "555-0100",
            "address": "1 Example Way",
        },
    )
    assert r.status_code == 200, r.text
    # resume_imports.text_enc / suggestions_enc
    r = await a.post(
        "/api/v1/vault/imports",
        content=make_pdf(["Jordan Rivera", "jordan@example.com", "Experience", "Analyst"]),
        headers={"content-type": "application/pdf", "x-filename": "resume.pdf"},
    )
    assert r.status_code == 201, r.text
    out["import_id"] = r.json()["id"]
    # contacts.*
    r = await a.post(
        "/api/v1/tracker/contacts",
        json={"name": "Sam Example", "email": "sam@example.com", "phone": "555-0101"},
    )
    out["contact_id"] = r.json()["id"]
    # attachments.content_enc
    app = (await a.post("/api/v1/tracker/applications", json={"title": "Analyst"})).json()
    out["pdf"] = make_pdf(["Cover letter"])
    r = await a.post(
        f"/api/v1/tracker/applications/{app['id']}/attachments",
        content=out["pdf"],
        headers={"content-type": "application/pdf", "x-filename": "letter.pdf"},
    )
    assert r.status_code == 201, r.text
    out["attachment_id"] = r.json()["id"]
    # job_sources.secret_enc
    r = await a.post(
        "/api/v1/discovery/sources",
        json={
            "kind": "jsearch",
            "name": "JSearch",
            "config": {"query": "analyst"},
            "secret": {"api_key": "sk-test-123"},
        },
    )
    assert r.status_code == 201, r.text
    out["source_id"] = r.json()["id"]
    # ai_settings.api_key_enc
    r = await a.put("/api/v1/ai/settings", json={"provider": "anthropic", "api_key": "sk-ant-x"})
    assert r.status_code == 200, r.text
    # notification_settings.*
    r = await a.put(
        "/api/v1/notifications/settings",
        json={
            "email_to": "jordan@example.com",
            "ntfy_url": "https://ntfy.sh/waypoint-rotation-test",
            "ntfy_token": "tk_secret",
            "webhook_kind": "discord",
            "webhook_url": "https://discord.com/api/webhooks/1/token",
        },
    )
    assert r.status_code == 200, r.text
    return out


async def test_rotate_then_drop_the_old_key(
    app_factory: Callable[[Settings], httpx.AsyncClient],
    make_settings: Callable[..., Settings],
) -> None:
    before = make_settings(encryption_keys=f"1:{OLD}", allow_registration=True)
    a, b = app_factory(before), app_factory(before)
    await _register(a, "jordan@example.com")
    await _register(b, "casey@example.com")
    seeded = await _fill_every_encrypted_column(a, b)

    maker = db.get_sessionmaker()
    async with maker() as s:
        usage = await rekey.key_usage(s)
    # Every encrypted column has a value, all on key 1.
    assert set(usage) == {f.name for f in rekey.FIELDS}
    assert rekey.totals(usage).keys() == {"1"}

    rotated = FieldCipher(f"2:{NEW},1:{OLD}")
    changed = await rekey.rekey(maker, rotated, batch_size=1)  # one row per batch
    assert set(changed) == {f.name for f in rekey.FIELDS}
    async with maker() as s:
        assert rekey.totals(await rekey.key_usage(s)).keys() == {"2"}
    assert await rekey.rekey(maker, rotated) == {}  # nothing left to do

    # The old key is gone: the app must still read everything.
    after = make_settings(encryption_keys=f"2:{NEW}", allow_registration=True)
    c = app_factory(after)
    await c.get("/api/v1/auth/status")
    r = await c.post(
        "/api/v1/auth/login", json={"email": "jordan@example.com", "password": TEST_PASSWORD}
    )
    assert r.json()["status"] == "mfa_required"
    code = pyotp.TOTP(seeded["totp"]).at(time.time() + 30)
    r = await c.post("/api/v1/auth/mfa/verify", json={"code": code})
    assert r.status_code == 200, r.text  # users.mfa_secret_enc decrypted with key 2

    profile = (await c.get("/api/v1/vault/profile")).json()
    assert (profile["email"], profile["phone"], profile["address"]) == (
        "jordan@example.com",
        "555-0100",
        "1 Example Way",
    )
    imp = await c.get(f"/api/v1/vault/imports/{seeded['import_id']}")
    assert imp.status_code == 200 and "Analyst" in imp.text
    contacts = (await c.get("/api/v1/tracker/contacts")).json()
    assert (contacts[0]["email"], contacts[0]["phone"]) == ("sam@example.com", "555-0101")
    file = await c.get(f"/api/v1/tracker/attachments/{seeded['attachment_id']}/download")
    assert file.content == seeded["pdf"]
    notify = (await c.get("/api/v1/notifications/settings")).json()
    assert notify["email_to"] == "jordan@example.com" and notify["has_ntfy_token"] is True
    assert notify["ntfy_url"] == "https://ntfy.sh/waypoint-rotation-test"
    assert notify["webhook_hint"] == "https://discord.com/…"

    # Values only the worker or the AI layer read: decrypt them directly with key 2 alone.
    only_new = FieldCipher(f"2:{NEW}")
    async with maker() as s:
        jordan = await s.scalar(select(User).where(User.email == "jordan@example.com"))
        casey = await s.scalar(select(User).where(User.email == "casey@example.com"))
        assert jordan is not None and casey is not None
        from waypoint.ai.models import AiSettings
        from waypoint.discovery.models import JobSource

        ai = await s.get(AiSettings, jordan.id)
        assert ai is not None and ai.api_key_enc
        assert only_new.decrypt(ai.api_key_enc, contexts.ai_key(jordan.id)) == "sk-ant-x"
        source = await s.get(JobSource, uuid.UUID(seeded["source_id"]))
        assert source is not None and source.secret_enc
        assert "sk-test-123" in only_new.decrypt(
            source.secret_enc, contexts.source_secret(jordan.id, source.id)
        )
        assert casey.mfa_pending_secret_enc
        assert (
            only_new.decrypt(casey.mfa_pending_secret_enc, contexts.mfa(casey.id, pending=True))
            == seeded["pending"]
        )


async def test_rekey_refuses_when_a_key_is_missing(
    user_client: httpx.AsyncClient, settings: Settings
) -> None:
    await user_client.post(
        "/api/v1/tracker/contacts", json={"name": "Sam Example", "email": "sam@example.com"}
    )
    # The test app writes with TEST_KEY as version 1; this keyring doesn't have it.
    with pytest.raises(rekey.RekeyError, match=r"contacts\.email_enc"):
        await rekey.rekey(db.get_sessionmaker(), FieldCipher(f"2:{NEW},3:{OLD}"))


def test_rotate_env_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sys.path.insert(0, str(BACKEND / "scripts"))
    import run_local  # type: ignore[import-not-found]

    env = tmp_path / ".env"
    env.write_text(f"# keep me\nWAYPOINT_ENCRYPTION_KEYS=1:{OLD}\nWAYPOINT_ENV=production\n")
    monkeypatch.setattr(run_local, "ENV_FILE", env)
    monkeypatch.delenv("WAYPOINT_ENCRYPTION_KEYS", raising=False)
    monkeypatch.setattr(run_local, "server_running", lambda port: False)
    assert run_local.rotate_env_key(8080) == "2"
    lines = env.read_text().splitlines()
    assert lines[0] == "# keep me" and lines[2] == "WAYPOINT_ENV=production"
    keyring = lines[1].split("=", 1)[1]
    assert keyring.startswith("2:") and keyring.endswith(f",1:{OLD}")
    FieldCipher(keyring)  # valid
    assert run_local.rotate_env_key(8080) == "3"

    monkeypatch.setattr(run_local, "server_running", lambda port: True)
    with pytest.raises(SystemExit, match="running"):
        run_local.rotate_env_key(8080)
    monkeypatch.setattr(run_local, "server_running", lambda port: False)
    monkeypatch.setenv("WAYPOINT_ENCRYPTION_KEYS", f"1:{OLD}")
    with pytest.raises(SystemExit, match="set in the environment"):
        run_local.rotate_env_key(8080)
