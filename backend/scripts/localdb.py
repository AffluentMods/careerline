# SPDX-License-Identifier: Apache-2.0
"""Run a private PostgreSQL 16 for local use, without Docker.

Uses the ``pgserver`` package (Apache-2.0), which bundles Postgres binaries.
The data directory lives in ``<repo>/.localdata/pg`` and is never committed.

    python scripts/localdb.py start   # start (or reuse) the server, create the DB, print the URL
    python scripts/localdb.py stop
    python scripts/localdb.py url
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import pgserver
from pgserver.postgres_server import PostgresServer
from pgserver.utils import PostmasterInfo

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / ".localdata" / "pg"
DB_NAME = "careerline"


RECOVERY_WAIT_SECONDS = 120


def _wait_until_ready(data: Path, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        info = PostmasterInfo.read_from_pgdata(data)
        if info is not None and info.is_running() and info.status == "ready":
            return True
        time.sleep(1)
    return False


def _server(data: Path) -> object:
    data.parent.mkdir(parents=True, exist_ok=True)
    # cleanup_mode=None leaves the server running after this script exits.
    try:
        return pgserver.get_server(data, cleanup_mode=None)
    except subprocess.TimeoutExpired:
        # After an unclean shutdown, Postgres replays its log before accepting
        # connections. On Windows that can take longer than pgserver's 10-second
        # start timeout (it briefly fights pg_ctl over the log file), but the
        # server keeps starting in the background. Wait for it, then reattach.
        print(
            "PostgreSQL is recovering from an unclean shutdown. "
            f"Waiting up to {RECOVERY_WAIT_SECONDS} seconds...",
            file=sys.stderr,
            flush=True,
        )
        if not _wait_until_ready(data, RECOVERY_WAIT_SECONDS):
            raise
        # pgserver caches the half-started instance; drop it so we reattach cleanly.
        PostgresServer._instances.pop(data.expanduser().resolve(), None)
        return pgserver.get_server(data, cleanup_mode=None)


def connection_url(srv: object, db_name: str) -> str:
    """A URL for asyncpg pointing at ``db_name`` on the embedded server.

    pgserver listens on TCP (127.0.0.1) on Windows, but only on a Unix socket on Linux and
    macOS, where it reports ``postgresql://postgres:@/postgres?host=<socket dir>``. Keep
    whichever it reports: asyncpg connects to the socket directory given as ``?host=``.
    """
    uri: str = srv.get_uri()  # type: ignore[attr-defined]
    parsed = urlparse(uri)
    user = quote(parsed.username or "postgres")
    socket_dir = parse_qs(parsed.query).get("host", [""])[0]
    if socket_dir:
        return f"postgresql://{user}@/{db_name}?host={quote(socket_dir, safe='/')}"
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 5432
    return f"postgresql://{user}@{host}:{port}/{db_name}"


def ensure_database(srv: object, db_name: str) -> None:
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", db_name):
        raise ValueError("Database name must be lowercase letters, digits, and underscores")
    # Safe: db_name is validated above; psql() takes no bind parameters.
    exists = srv.psql(f"SELECT 1 FROM pg_database WHERE datname = '{db_name}';")  # type: ignore[attr-defined]  # noqa: S608
    if "(1 row)" not in exists:
        srv.psql(f'CREATE DATABASE "{db_name}";')  # type: ignore[attr-defined]


def stop(data: Path) -> int:
    """Stop the server for ``data`` no matter which process started it."""
    if not (data / "postmaster.pid").exists():
        print("not running")
        return 0
    exe = "pg_ctl.exe" if sys.platform == "win32" else "pg_ctl"
    pg_ctl = Path(pgserver.__file__).parent / "pginstall" / "bin" / exe
    result = subprocess.run(  # noqa: S603
        [str(pg_ctl), "stop", "-D", str(data), "-m", "fast", "-w"],
        capture_output=True,
        text=True,
        check=False,
    )
    print("stopped" if result.returncode == 0 else result.stderr.strip())
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["start", "stop", "url", "raw"])
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--db", default=DB_NAME)
    args = parser.parse_args()

    if args.command == "stop":
        return stop(args.data)
    srv = _server(args.data)
    if args.command == "raw":
        print(srv.get_uri())  # type: ignore[attr-defined]
        return 0
    ensure_database(srv, args.db)
    print(connection_url(srv, args.db))
    return 0


if __name__ == "__main__":
    sys.exit(main())
