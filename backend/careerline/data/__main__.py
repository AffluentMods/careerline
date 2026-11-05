# SPDX-License-Identifier: Apache-2.0
"""Backups and encryption keys from the command line.

    python -m careerline.data backup             # write a backup now (needs CAREERLINE_BACKUP_DIR)
    python -m careerline.data verify FILE        # decrypt and check a backup, print what's in it
    python -m careerline.data restore FILE       # load a backup into an empty, migrated database
    python -m careerline.data keys               # which encryption key versions are in use
    python -m careerline.data rekey              # re-encrypt everything with the first key

Restoring: point CAREERLINE_DATABASE_URL at a fresh database, run ``alembic upgrade head``
with the same CareerLine version that made the backup, and keep the same
CAREERLINE_ENCRYPTION_KEYS (and CAREERLINE_BACKUP_KEY, if you set one).

Retiring a key: put the new key first in CAREERLINE_ENCRYPTION_KEYS, stop CareerLine, run
``rekey``, then remove the old key once ``keys`` shows it's unused. Backups written before
that still need the old key (unless CAREERLINE_BACKUP_KEY is set).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from careerline import db
from careerline.config import Settings, get_settings
from careerline.data import backup, rekey
from careerline.security.crypto import FieldCipher


def _print_usage(usage: dict[str, dict[str, int]], cipher: FieldCipher, settings: Settings) -> int:
    """Print key usage; return how many values are not on the active key."""
    active = cipher.active_version
    totals = rekey.totals(usage)
    if not totals:
        print("  No encrypted values are stored yet.")
    for version, n in totals.items():
        label = " (active)" if version == active else ""
        print(f"  key {version}{label}: {n:,} value{'s' if n != 1 else ''}")
    unknown = [v for v in totals if v not in cipher.versions]
    if unknown:
        print(f"  Not in CAREERLINE_ENCRYPTION_KEYS: {', '.join(unknown)}. Those can't be read.")
    stale = sum(n for v, n in totals.items() if v != active)
    unused = [v for v in cipher.versions if v != active and v not in totals]
    if unknown:
        print("Add the missing key back to CAREERLINE_ENCRYPTION_KEYS (after the first one).")
    elif stale:
        noun = "value still uses" if stale == 1 else "values still use"
        print(f"{stale:,} {noun} an older key. Run: python -m careerline.data rekey")
    elif unused:
        print(
            f"Nothing uses key {', '.join(unused)} any more: you can remove it from "
            "CAREERLINE_ENCRYPTION_KEYS."
        )
        if not settings.backup_key:
            print(
                "  Backups written before you re-encrypted still need it, so keep a copy "
                "until those backups have expired."
            )
    return stale


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    if args.command == "verify":
        with backup.opened(Path(args.file), settings) as (_, manifest):
            made_by, made_at = manifest.get("app_version"), manifest.get("created_at")
            print(f"OK: CareerLine {made_by} backup from {made_at}")
            print(f"Database version: {manifest.get('schema_revision')}")
            for name, count in sorted(manifest.get("counts", {}).items()):
                if count:
                    print(f"  {name}: {count}")
        return 0
    cipher = FieldCipher(settings.encryption_keys.get_secret_value())
    db.init_engine(settings.async_database_url)
    try:
        sessionmaker = db.get_sessionmaker()
        if args.command == "backup":
            made = await backup.create_backup(sessionmaker, settings)
            print(f"Wrote {made.name} ({made.size:,} bytes)")
        elif args.command == "restore":
            counts = await backup.restore_backup(sessionmaker, settings, Path(args.file))
            tables = sum(1 for n in counts.values() if n)
            print(f"Restored {sum(counts.values()):,} rows into {tables} tables.")
        elif args.command == "keys":
            async with sessionmaker() as session:
                usage = await rekey.key_usage(session)
            print("Encrypted values by key version:")
            _print_usage(usage, cipher, settings)
        else:
            print(f"Re-encrypting with key {cipher.active_version}...")
            changed = await rekey.rekey(sessionmaker, cipher)
            for name, n in changed.items():
                print(f"  {name}: {n:,}")
            print(f"Re-encrypted {sum(changed.values()):,} values.")
            async with sessionmaker() as session:
                usage = await rekey.key_usage(session)
            print("Encrypted values by key version now:")
            if _print_usage(usage, cipher, settings):
                print("Some values changed while this ran. Stop CareerLine and run it again.")
                return 1
    finally:
        await db.dispose_engine()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m careerline.data",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("backup", help="write a backup now")
    for name in ("verify", "restore"):
        p = sub.add_parser(name)
        p.add_argument("file")
    sub.add_parser("keys", help="show which encryption key versions are in use")
    sub.add_parser("rekey", help="re-encrypt everything with the first (active) key")
    args = parser.parse_args()
    try:
        return asyncio.run(_run(args))
    except (backup.BackupError, rekey.RekeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
