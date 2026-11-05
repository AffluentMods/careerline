# SPDX-License-Identifier: Apache-2.0
"""Alembic environment. The database URL comes from CAREERLINE_DATABASE_URL (never from alembic.ini)."""

import asyncio
import os

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from careerline import model_registry  # noqa: F401  (registers every table on Base.metadata)
from careerline.db import Base


def _url() -> str:
    url = context.config.attributes.get("database_url") or os.environ.get("CAREERLINE_DATABASE_URL")
    if not url:
        file_path = os.environ.get("CAREERLINE_DATABASE_URL_FILE")
        if file_path:
            with open(file_path, encoding="utf-8") as fh:
                url = fh.read().strip()
    if not url:
        raise RuntimeError("Set CAREERLINE_DATABASE_URL to run migrations")
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            url = "postgresql+asyncpg://" + url[len(prefix) :]
    return url


def _run(connection) -> None:  # type: ignore[no-untyped-def]
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def _main() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as conn:
        await conn.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=_url(), target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(_main())
