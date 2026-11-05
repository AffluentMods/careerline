# SPDX-License-Identifier: Apache-2.0
"""Encrypted backups: the stream format, scheduling, the owner API, and a full restore."""

from __future__ import annotations

import asyncio
import io
import os
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.conftest import BACKEND, TEST_PASSWORD
from waypoint import db
from waypoint.config import Settings
from waypoint.data import backup
from waypoint.data.jobs import schedule_backup
from waypoint.discovery.models import QueuedJob
from waypoint.models import AuditLog, User
from waypoint.net import SafeFetcher
from waypoint.security.crypto import FieldCipher, generate_key
from waypoint.worker.runner import WorkerContext, drain

KEY = os.urandom(32)


def _roundtrip(data: bytes, key: bytes = KEY) -> bytes:
    enc = io.BytesIO()
    backup.encrypt_stream(io.BytesIO(data), enc, key)
    out = io.BytesIO()
    backup.decrypt_stream(io.BytesIO(enc.getvalue()), out, key)
    return out.getvalue()


@pytest.mark.parametrize("size", [0, 1, 100, 4096, 4096 * 3 + 7])
def test_stream_roundtrip(monkeypatch: pytest.MonkeyPatch, size: int) -> None:
    monkeypatch.setattr(backup, "CHUNK", 4096)
    data = os.urandom(size)
    assert _roundtrip(data) == data


def _encrypted(monkeypatch: pytest.MonkeyPatch, data: bytes) -> bytes:
    monkeypatch.setattr(backup, "CHUNK", 1024)
    enc = io.BytesIO()
    backup.encrypt_stream(io.BytesIO(data), enc, KEY)
    return enc.getvalue()


def _decrypt(blob: bytes, key: bytes = KEY) -> bytes:
    out = io.BytesIO()
    backup.decrypt_stream(io.BytesIO(blob), out, key)
    return out.getvalue()


def test_detects_tampering_truncation_and_wrong_key(monkeypatch: pytest.MonkeyPatch) -> None:
    data = os.urandom(5000)
    blob = _encrypted(monkeypatch, data)
    head = len(backup.MAGIC) + backup.SALT_BYTES
    chunk = 4 + 1024 + 16

    flipped = bytearray(blob)
    flipped[head + 10] ^= 1
    with pytest.raises(backup.BackupError, match="damaged, or the key"):
        _decrypt(bytes(flipped))
    # Dropping whole final chunks: the new last chunk wasn't encrypted as "last".
    with pytest.raises(backup.BackupError):
        _decrypt(blob[: head + 2 * chunk])
    with pytest.raises(backup.BackupError, match="truncated"):
        _decrypt(blob[:-5])
    with pytest.raises(backup.BackupError):
        _decrypt(blob + blob[head : head + chunk])  # an extra chunk appended
    with pytest.raises(backup.BackupError, match="damaged, or the key"):
        _decrypt(blob, os.urandom(32))
    with pytest.raises(backup.BackupError, match="isn't a Waypoint backup"):
        _decrypt(b"PK\x03\x04" + blob)
    # Swapping two chunks breaks it too.
    first, second = blob[head : head + chunk], blob[head + chunk : head + 2 * chunk]
    with pytest.raises(backup.BackupError):
        _decrypt(blob[:head] + second + first + blob[head + 2 * chunk :])
    assert _decrypt(blob) == data


def test_key_derivation(make_settings: Callable[..., Settings]) -> None:
    a = make_settings()
    b = make_settings(backup_key=generate_key())
    assert backup.master_key(a) != backup.master_key(b)
    assert backup.master_key(a) == backup.master_key(make_settings())
    assert len(backup.master_key(b)) == 32


def _ctx(settings: Settings) -> WorkerContext:
    return WorkerContext(
        sessionmaker=db.get_sessionmaker(),
        settings=settings,
        cipher=FieldCipher(settings.encryption_keys.get_secret_value()),
        fetcher_factory=lambda: SafeFetcher(),
    )


async def test_schedule_write_prune_and_restore(
    user_client: httpx.AsyncClient,
    make_settings: Callable[..., Settings],
    tmp_path: Path,
    migrated: str,
) -> None:
    c = user_client
    await c.post("/api/v1/vault/entries", json={"kind": "skill", "data": {"name": "SQL"}})
    # A child stored before its parent: updating the parent moves its row to the end.
    role = (
        await c.post(
            "/api/v1/vault/entries",
            json={"kind": "role", "data": {"title": "Analyst", "employer": "Litware"}},
        )
    ).json()
    await c.post(
        "/api/v1/vault/entries",
        json={"kind": "achievement", "parent_id": role["id"], "data": {"text": "Did a thing"}},
    )
    await c.patch(f"/api/v1/vault/entries/{role['id']}", json={"evidence_note": "offer letter"})
    await c.post(
        "/api/v1/tracker/contacts", json={"name": "Sam Example", "email": "sam@example.com"}
    )
    settings = make_settings(backup_dir=str(tmp_path), backup_keep=2)
    ctx = _ctx(settings)

    async with db.get_sessionmaker()() as s:
        assert await schedule_backup(ctx, s) == 1
        await s.commit()
        assert await schedule_backup(ctx, s) == 0  # already queued
    await drain(ctx)
    files = backup.list_backups(tmp_path)
    assert len(files) == 1
    raw = (tmp_path / files[0].name).read_bytes()
    assert raw.startswith(backup.MAGIC)
    assert b"sam@example.com" not in raw and b"SQL" not in raw
    async with db.get_sessionmaker()() as s:
        assert await schedule_backup(ctx, s) == 0  # a fresh backup exists

    # Retention keeps the newest two.
    for stamp in ("20200101-000000", "20200102-000000"):
        (tmp_path / f"waypoint-{stamp}.wpbackup").write_bytes(b"old")
    backup.prune(tmp_path, 2)
    assert [f.name for f in backup.list_backups(tmp_path)] == [
        files[0].name,
        "waypoint-20200102-000000.wpbackup",
    ]

    # Restore into a brand-new database migrated to the same version.
    target = migrated.rsplit("/", 1)[0] + "/waypoint_restore_test"
    admin = create_async_engine(
        Settings(
            _env_file=None, database_url=migrated, encryption_keys="1:" + generate_key()
        ).async_database_url,  # type: ignore[call-arg]
        isolation_level="AUTOCOMMIT",
    )
    async with admin.connect() as conn:
        await conn.execute(text("DROP DATABASE IF EXISTS waypoint_restore_test WITH (FORCE)"))
        await conn.execute(text("CREATE DATABASE waypoint_restore_test"))
    try:
        cfg = Config(str(BACKEND / "alembic.ini"))
        cfg.attributes["database_url"] = target
        await asyncio.to_thread(command.upgrade, cfg, "head")
        restore_settings = make_settings(database_url=target, backup_dir=str(tmp_path))
        engine = create_async_engine(restore_settings.async_database_url)
        try:
            maker = async_sessionmaker(engine, expire_on_commit=False)
            counts = await backup.restore_backup(maker, restore_settings, tmp_path / files[0].name)
            assert counts["users"] == 1 and counts["vault_entries"] == 3
            async with maker() as s:
                user = await s.scalar(select(User))
                assert user is not None and user.email == "jordan@example.com"
                # The audit log keeps numbering after the restored rows.
                before = await s.scalar(select(func.max(AuditLog.id)))
                s.add(AuditLog(user_id=user.id, event="test.after_restore", details={}))
                await s.commit()
                after = await s.scalar(select(func.max(AuditLog.id)))
                assert before is not None and after is not None and after > before
            # Restoring twice is refused: the database isn't empty any more.
            with pytest.raises(backup.BackupError, match="isn't empty"):
                await backup.restore_backup(maker, restore_settings, tmp_path / files[0].name)
        finally:
            await engine.dispose()

        # The restored server works: sign in with the same password.
        app_settings = make_settings(database_url=target)
        from waypoint.main import create_app

        await db.dispose_engine()
        db.init_engine(app_settings.async_database_url)
        app = create_app(app_settings)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://testserver"
        ) as fresh:
            await fresh.get("/api/v1/auth/status")
            token = fresh.cookies.get(app_settings.csrf_cookie) or ""
            r = await fresh.post(
                "/api/v1/auth/login",
                json={"email": "jordan@example.com", "password": TEST_PASSWORD},
                headers={"x-csrf-token": token},
            )
            assert r.status_code == 200, r.text
            contacts = (await fresh.get("/api/v1/tracker/contacts")).json()
            assert contacts[0]["email"] == "sam@example.com"
        await db.dispose_engine()
        db.init_engine(make_settings().async_database_url)
    finally:
        async with admin.connect() as conn:
            await conn.execute(text("DROP DATABASE IF EXISTS waypoint_restore_test WITH (FORCE)"))
        await admin.dispose()


async def test_restore_refuses_other_versions(
    user_client: httpx.AsyncClient, make_settings: Callable[..., Settings], tmp_path: Path
) -> None:
    settings = make_settings(backup_dir=str(tmp_path))
    made = await backup.create_backup(db.get_sessionmaker(), settings)
    async with db.get_sessionmaker()() as s:
        await s.execute(text("UPDATE alembic_version SET version_num = '0001'"))
        await s.commit()
    try:
        with pytest.raises(backup.BackupError, match="same Waypoint version"):
            await backup.restore_backup(db.get_sessionmaker(), settings, tmp_path / made.name)
    finally:
        async with db.get_sessionmaker()() as s:
            await s.execute(text("UPDATE alembic_version SET version_num = :v"), {"v": "0008"})
            await s.commit()


async def test_backup_api_is_owner_only(
    two_users: tuple[httpx.AsyncClient, httpx.AsyncClient],
    app_factory: Callable[[Settings], httpx.AsyncClient],
    make_settings: Callable[..., Settings],
) -> None:
    owner, other = two_users
    r = await owner.get("/api/v1/data/backups")
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert (await owner.post("/api/v1/data/backups")).status_code == 409
    assert (await other.get("/api/v1/data/backups")).status_code == 403
    assert (await other.post("/api/v1/data/backups")).status_code == 403


async def test_backup_now_queues_a_job(
    app_factory: Callable[[Settings], httpx.AsyncClient],
    make_settings: Callable[..., Settings],
    tmp_path: Path,
) -> None:
    s = make_settings(backup_dir=str(tmp_path))
    c = app_factory(s)
    await c.get("/api/v1/auth/status")
    await c.post(
        "/api/v1/auth/register",
        json={"email": "jordan@example.com", "password": TEST_PASSWORD, "display_name": "J"},
    )
    r = await c.post("/api/v1/data/backups")
    assert r.status_code == 202 and r.json() == {"queued": True}
    await drain(_ctx(s))
    status = (await c.get("/api/v1/data/backups")).json()
    assert status["enabled"] is True and len(status["files"]) == 1
    assert status["last_job_status"] == "done"
    async with db.get_sessionmaker()() as session:
        kinds = await session.scalars(select(QueuedJob.kind))
        assert "backup" in set(kinds)


def test_old_backups_open_after_key_rotation(make_settings: Callable[..., Settings]) -> None:
    old_key, new_key = generate_key(), generate_key()
    before = make_settings(encryption_keys=f"1:{old_key}")
    after = make_settings(encryption_keys=f"2:{new_key},1:{old_key}")
    enc = io.BytesIO()
    backup.encrypt_stream(io.BytesIO(b"payload"), enc, backup.master_key(before))
    out = io.BytesIO()
    backup.decrypt_stream(io.BytesIO(enc.getvalue()), out, backup.master_keys(after))
    assert out.getvalue() == b"payload"
    assert backup.master_key(after) != backup.master_key(before)  # new backups use the new key
    with pytest.raises(backup.BackupError):
        backup.decrypt_stream(
            io.BytesIO(enc.getvalue()), io.BytesIO(), backup.master_keys(make_settings())
        )


def test_every_table_is_backed_up_from_a_fresh_process() -> None:
    """The CLI and worker import only some modules; backups must still see every table."""
    import subprocess
    import sys

    code = (
        "from waypoint.data import backup, portable; "
        "print(','.join(sorted(t.name for t in backup.backup_tables()))); "
        "print(','.join(sorted(t.name for t in portable.export_tables())))"
    )
    out = subprocess.run(  # noqa: S603 - our own interpreter and a fixed script
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=BACKEND
    ).stdout.split()
    from waypoint import model_registry  # noqa: F401
    from waypoint.db import Base

    expected = {t.name for t in Base.metadata.sorted_tables} - backup.EXCLUDED
    assert set(out[0].split(",")) == expected
    assert {"applications", "job_postings", "documents", "notification_settings"} <= expected
    from waypoint.data import portable

    assert set(out[1].split(",")) == set(portable.EXPORT_TABLES)
