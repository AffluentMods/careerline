# SPDX-License-Identifier: Apache-2.0
"""Run the background worker:  python -m waypoint.worker"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from waypoint import db
from waypoint.config import get_settings
from waypoint.net import fetcher_factory
from waypoint.security.crypto import FieldCipher
from waypoint.worker import handlers  # noqa: F401  (registers every job handler)
from waypoint.worker.runner import WorkerContext, run_forever


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = get_settings()
    db.init_engine(settings.async_database_url)
    sessionmaker = db.get_sessionmaker()
    ctx = WorkerContext(
        sessionmaker=sessionmaker,
        settings=settings,
        cipher=FieldCipher(settings.encryption_keys.get_secret_value()),
        fetcher_factory=fetcher_factory(settings),
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # Windows has no add_signal_handler
            loop.add_signal_handler(sig, stop.set)
    logging.getLogger("waypoint.worker").info("worker %s started", ctx.worker_id)
    try:
        await run_forever(ctx, stop)
    finally:
        await db.dispose_engine()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
