# SPDX-License-Identifier: Apache-2.0
"""App factory. Run with: ``uvicorn waypoint.main:create_app --factory``."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from waypoint import __version__, db
from waypoint.ai.routes import router as ai_router
from waypoint.assistant.routes import router as assistant_router
from waypoint.auth.oidc import router as oidc_router
from waypoint.auth.routes import router as auth_router
from waypoint.auth.sessions import DB
from waypoint.config import Settings, get_settings
from waypoint.data.routes import IMPORT_PATH as DATA_IMPORT_PATH
from waypoint.data.routes import router as data_router
from waypoint.discovery.routes import router as discovery_router
from waypoint.errors import ApiError, install_error_handlers
from waypoint.insights.routes import router as insights_router
from waypoint.materials.routes import router as materials_router
from waypoint.net import SafeFetcher, fetcher_factory
from waypoint.notify.routes import router as notify_router
from waypoint.security.crypto import FieldCipher
from waypoint.security.middleware import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from waypoint.tracker.files import UPLOAD_PATH_PATTERN
from waypoint.tracker.files import router as tracker_files_router
from waypoint.tracker.people import router as tracker_people_router
from waypoint.tracker.routes import router as tracker_router
from waypoint.vault.imports import IMPORT_PATH
from waypoint.vault.imports import router as import_router
from waypoint.vault.routes import router as vault_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    docs = settings.env == "development"

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db.init_engine(settings.async_database_url)
        if settings.demo_mode:
            await _seed_demo(app.state.cipher)
        try:
            yield
        finally:
            await db.dispose_engine()

    app = FastAPI(
        title="Waypoint",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs" if docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if docs else None,
    )
    app.state.settings = settings
    app.state.cipher = FieldCipher(settings.encryption_keys.get_secret_value())
    app.state.fetcher_factory = fetcher_factory(settings)
    # The sign-in provider is configured by the admin, so it may live on the LAN.
    app.state.oidc_fetcher_factory = lambda: SafeFetcher(allow_private=True)

    install_error_handlers(app)
    app.include_router(auth_router)
    app.include_router(vault_router)
    app.include_router(import_router)
    app.include_router(tracker_router)
    app.include_router(tracker_people_router)
    app.include_router(tracker_files_router)
    app.include_router(discovery_router)
    app.include_router(ai_router)
    app.include_router(materials_router)
    app.include_router(assistant_router)
    app.include_router(insights_router)
    app.include_router(notify_router)
    app.include_router(data_router)
    app.include_router(oidc_router)

    @app.get("/api/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/readyz", include_in_schema=False)
    async def readyz(session: DB) -> dict[str, str]:
        await session.execute(text("SELECT 1"))
        return {"status": "ok"}

    if settings.static_dir is not None:
        _mount_spa(app, settings.static_dir)

    # Added last = outermost, so headers apply even to 413 and error responses.
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_bytes=settings.max_body_bytes,
        overrides=[
            ("POST", re.escape(IMPORT_PATH), settings.import_max_bytes),
            ("POST", UPLOAD_PATH_PATTERN, settings.attachment_max_bytes),
            ("POST", re.escape(DATA_IMPORT_PATH), settings.import_archive_max_bytes),
        ],
    )
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.hsts, docs_enabled=docs)
    return app


async def _seed_demo(cipher: FieldCipher) -> None:
    from waypoint.demo import seed_demo

    async with db.get_sessionmaker()() as session:
        await seed_demo(session, cipher)
        await session.commit()


def _mount_spa(app: FastAPI, static_dir: Path) -> None:
    root = static_dir.resolve()
    index_file = root / "index.html"
    if not index_file.is_file():
        raise RuntimeError(f"WAYPOINT_STATIC_DIR has no index.html: {root}")
    if (root / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=root / "assets"), name="assets")

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def spa(path: str) -> Response:
        if path == "api" or path.startswith("api/"):
            raise ApiError(404, "not_found", "Not found.")
        if path:
            candidate = (root / path).resolve()
            if candidate.is_file() and candidate.is_relative_to(root):
                return FileResponse(candidate)
        return HTMLResponse(
            index_file.read_text(encoding="utf-8"), headers={"Cache-Control": "no-cache"}
        )
