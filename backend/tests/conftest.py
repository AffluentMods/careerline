# SPDX-License-Identifier: Apache-2.0
"""Test fixtures.

Tests run against a real PostgreSQL. Set WAYPOINT_TEST_DATABASE_URL to use an
existing server (CI does this). Otherwise a private server is started with
pgserver in ``.localdata/pg-test``.
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from waypoint import db
from waypoint.config import Settings
from waypoint.main import create_app
from waypoint.security.crypto import generate_key

BACKEND = Path(__file__).resolve().parents[1]
TEST_KEY = generate_key()


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    url = os.environ.get("WAYPOINT_TEST_DATABASE_URL")
    if url:
        yield url
        return
    sys.path.insert(0, str(BACKEND / "scripts"))
    import localdb  # type: ignore[import-not-found]

    data = BACKEND.parent / ".localdata" / "pg-test"
    srv = localdb._server(data)
    srv.psql("DROP DATABASE IF EXISTS waypoint_test WITH (FORCE);")
    localdb.ensure_database(srv, "waypoint_test")
    yield localdb.tcp_url(srv, "waypoint_test")


@pytest.fixture(scope="session")
def migrated(database_url: str) -> str:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.attributes["database_url"] = database_url
    command.upgrade(cfg, "head")
    return database_url


@pytest.fixture
def make_settings(migrated: str) -> Callable[..., Settings]:
    def _make(**overrides: object) -> Settings:
        values: dict[str, object] = {
            "env": "test",
            "database_url": migrated,
            "encryption_keys": f"1:{TEST_KEY}",
            "cookie_secure": True,
            "hsts": True,
        }
        values.update(overrides)
        return Settings(_env_file=None, **values)  # type: ignore[arg-type, call-arg]

    return _make


@pytest.fixture
def settings(make_settings: Callable[..., Settings]) -> Settings:
    return make_settings()


@pytest.fixture
async def app_factory(
    migrated: str,
) -> AsyncIterator[Callable[[Settings], httpx.AsyncClient]]:
    base = Settings(_env_file=None, database_url=migrated, encryption_keys=f"1:{TEST_KEY}")  # type: ignore[call-arg]
    engine = db.init_engine(base.async_database_url)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE users, rate_limit_hits, audit_log, jobs CASCADE"))
    clients: list[httpx.AsyncClient] = []

    def _client(s: Settings) -> httpx.AsyncClient:
        app = create_app(s)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("203.0.113.10", 5000)),
            base_url="https://testserver",
        )

        async def add_csrf(request: httpx.Request) -> None:
            token = client.cookies.get(s.csrf_cookie)
            if token and "x-csrf-token" not in request.headers:
                request.headers["x-csrf-token"] = token

        client.event_hooks["request"].append(add_csrf)
        clients.append(client)
        return client

    yield _client
    for c in clients:
        await c.aclose()
    await db.dispose_engine()


@pytest.fixture
async def client(
    app_factory: Callable[[Settings], httpx.AsyncClient], settings: Settings
) -> httpx.AsyncClient:
    c = app_factory(settings)
    await c.get("/api/v1/auth/status")  # obtain a CSRF cookie
    return c


TEST_PASSWORD = "correct horse battery staple"


async def _signed_in(
    app_factory: Callable[[Settings], httpx.AsyncClient], s: Settings, email: str, name: str
) -> httpx.AsyncClient:
    c = app_factory(s)
    await c.get("/api/v1/auth/status")
    r = await c.post(
        "/api/v1/auth/register",
        json={"email": email, "password": TEST_PASSWORD, "display_name": name},
    )
    assert r.status_code == 201, r.text
    return c


@pytest.fixture
async def user_client(
    app_factory: Callable[[Settings], httpx.AsyncClient], settings: Settings
) -> httpx.AsyncClient:
    """A client signed in as a freshly registered owner (fictional person)."""
    return await _signed_in(app_factory, settings, "jordan@example.com", "Jordan Rivera")


@pytest.fixture
async def two_users(
    app_factory: Callable[[Settings], httpx.AsyncClient],
    make_settings: Callable[..., Settings],
) -> tuple[httpx.AsyncClient, httpx.AsyncClient]:
    s = make_settings(allow_registration=True)
    a = await _signed_in(app_factory, s, "jordan@example.com", "Jordan Rivera")
    b = await _signed_in(app_factory, s, "casey@example.com", "Casey Lin")
    return a, b
