# SPDX-License-Identifier: Apache-2.0
"""Encrypted backups of the whole database, and restoring them.

A backup is a zip of every table (one JSON line per row, values exactly as stored, so
encrypted fields stay encrypted), encrypted as a stream of 1 MiB chunks with AES-256-GCM:

    magic (16) | salt (32) | { length (4, big-endian) | ciphertext+tag }...

Each file gets its own key, derived with HKDF-SHA256 from the backup key and the file's
random salt. A chunk's nonce is its counter plus a final-chunk flag, so reordering,
dropping, or truncating chunks makes decryption fail (the STREAM construction).

The backup key is ``WAYPOINT_BACKUP_KEY``, or else derived from the first encryption key.
Restoring needs the same encryption keys anyway (field values stay encrypted with them), so
keeping your ``.env`` safe is enough. Login sessions, rate-limit counters and the job queue
are not backed up.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import Integer, Table, exists, func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from waypoint import __version__, model_registry
from waypoint.config import Settings
from waypoint.data.codec import DecodeError, decode, encode_row
from waypoint.db import Base
from waypoint.models import User
from waypoint.security.crypto import decode_key, parse_keyring

_ = model_registry  # imported for its side effect: every table is registered

MAGIC = b"WAYPOINT-BACKUP1"
SALT_BYTES = 32
CHUNK = 1 << 20
SUFFIX = ".wpbackup"
PREFIX = "waypoint-"
EXCLUDED = frozenset({"auth_sessions", "rate_limit_hits", "jobs"})
FORMAT = "waypoint-backup"


class BackupError(Exception):
    pass


def backup_tables() -> list[Table]:
    return [t for t in Base.metadata.sorted_tables if t.name not in EXCLUDED]


def _derive(field_key: bytes) -> bytes:
    return HKDF(algorithm=SHA256(), length=32, salt=None, info=b"waypoint backup key v1").derive(
        field_key
    )


def master_key(settings: Settings) -> bytes:
    """The key new backups are written with."""
    return master_keys(settings)[0]


def master_keys(settings: Settings) -> list[bytes]:
    """Every key a backup may have been written with, newest first. After rotating
    WAYPOINT_ENCRYPTION_KEYS (new key first, old key kept), old backups still open."""
    keys = [decode_key(settings.backup_key.get_secret_value())] if settings.backup_key else []
    keys += [_derive(k) for _, k in parse_keyring(settings.encryption_keys.get_secret_value())]
    return keys


def _file_key(master: bytes, salt: bytes) -> AESGCM:
    key = HKDF(algorithm=SHA256(), length=32, salt=salt, info=b"waypoint backup file v1").derive(
        master
    )
    return AESGCM(key)


def _nonce(counter: int, last: bool) -> bytes:
    if counter >= 1 << 88:  # pragma: no cover - 2^88 MiB
        raise BackupError("backup too large")
    return counter.to_bytes(11, "big") + (b"\x01" if last else b"\x00")


def encrypt_stream(src: IO[bytes], dst: IO[bytes], master: bytes) -> None:
    salt = os.urandom(SALT_BYTES)
    aead = _file_key(master, salt)
    dst.write(MAGIC + salt)
    counter = 0
    chunk = src.read(CHUNK)
    while True:
        following = src.read(CHUNK)
        last = not following
        ct = aead.encrypt(_nonce(counter, last), chunk, MAGIC)
        dst.write(len(ct).to_bytes(4, "big") + ct)
        if last:
            return
        chunk, counter = following, counter + 1


def decrypt_stream(src: IO[bytes], dst: IO[bytes], master: bytes | list[bytes]) -> None:
    """Decrypt with ``master``, or with whichever of several candidate keys opens the file."""
    if src.read(len(MAGIC)) != MAGIC:
        raise BackupError("That file isn't a Waypoint backup.")
    salt = src.read(SALT_BYTES)
    if len(salt) != SALT_BYTES:
        raise BackupError("The backup is truncated.")
    candidates = [_file_key(m, salt) for m in (master if isinstance(master, list) else [master])]

    def read_chunk() -> bytes | None:
        head = src.read(4)
        if not head:
            return None
        size = int.from_bytes(head, "big") if len(head) == 4 else -1
        if not 16 <= size <= CHUNK + 16:
            raise BackupError("The backup is damaged.")
        body = src.read(size)
        if len(body) != size:
            raise BackupError("The backup is truncated.")
        return body

    current = read_chunk()
    if current is None:
        raise BackupError("The backup is truncated.")
    counter = 0
    aead: AESGCM | None = None
    while current is not None:
        following = read_chunk()
        nonce = _nonce(counter, following is None)
        plain = None
        # The first chunk picks the key; every later chunk must use the same one.
        for candidate in [aead] if aead else candidates:
            try:
                plain = candidate.decrypt(nonce, current, MAGIC)
                aead = candidate
                break
            except InvalidTag:
                continue
        if plain is None:
            raise BackupError(
                "The backup couldn't be decrypted: it's damaged, or the key is different."
            )
        dst.write(plain)
        current, counter = following, counter + 1


# --- creating ------------------------------------------------------------------------------------


@dataclass
class BackupFile:
    name: str
    size: int
    created_at: datetime


def list_backups(directory: Path) -> list[BackupFile]:
    if not directory.is_dir():
        return []
    files = []
    for p in directory.glob(f"{PREFIX}*{SUFFIX}"):
        stat = p.stat()
        files.append(
            BackupFile(p.name, stat.st_size, datetime.fromtimestamp(stat.st_mtime, tz=UTC))
        )
    return sorted(files, key=lambda f: f.name, reverse=True)


async def _dump(db: AsyncSession, zf: zipfile.ZipFile) -> dict[str, Any]:
    # One snapshot for every table, so the backup is consistent.
    await db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    revision = str(await db.scalar(text("SELECT version_num FROM alembic_version")) or "")
    counts: dict[str, int] = {}
    for table in backup_tables():
        n = 0
        with zf.open(f"tables/{table.name}.jsonl", "w", force_zip64=True) as fh:
            result = await db.stream(select(table).execution_options(yield_per=500))
            async for row in result:
                fh.write(json.dumps(encode_row(table, row), ensure_ascii=False).encode() + b"\n")
                n += 1
        counts[table.name] = n
    manifest = {
        "format": FORMAT,
        "version": 1,
        "app_version": __version__,
        "schema_revision": revision,
        "created_at": datetime.now(UTC).isoformat(),
        "counts": counts,
    }
    zf.writestr("manifest.json", json.dumps(manifest, indent=2))
    return manifest


async def create_backup(
    sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
) -> BackupFile:
    if settings.backup_dir is None:
        raise BackupError("Backups are off (WAYPOINT_BACKUP_DIR is not set).")
    directory = settings.backup_dir
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    final = directory / f"{PREFIX}{stamp}{SUFFIX}"
    partial = final.with_name(final.name + ".partial")
    fd, plain_name = tempfile.mkstemp(prefix="waypoint-backup-", suffix=".zip")
    plain = Path(plain_name)
    try:
        with os.fdopen(fd, "w+b") as plain_fh:
            with zipfile.ZipFile(plain_fh, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                async with sessionmaker() as db:
                    await _dump(db, zf)
            plain_fh.seek(0)
            with partial.open("wb") as out:
                encrypt_stream(plain_fh, out, master_key(settings))
                out.flush()
                os.fsync(out.fileno())
        os.replace(partial, final)
    finally:
        _discard(plain, partial)
    prune(directory, settings.backup_keep)
    stat = final.stat()
    return BackupFile(final.name, stat.st_size, datetime.fromtimestamp(stat.st_mtime, tz=UTC))


def _discard(*paths: Path) -> None:
    for p in paths:
        p.unlink(missing_ok=True)


def prune(directory: Path, keep: int) -> list[str]:
    removed = []
    for f in list_backups(directory)[keep:]:
        (directory / f.name).unlink(missing_ok=True)
        removed.append(f.name)
    return removed


# --- restoring ---------------------------------------------------------------------------------


@contextlib.contextmanager
def opened(path: Path, settings: Settings) -> Iterator[tuple[zipfile.ZipFile, dict[str, Any]]]:
    """Decrypt a backup to a temporary zip and yield it with its manifest."""
    fd, plain_name = tempfile.mkstemp(prefix="waypoint-restore-", suffix=".zip")
    plain = Path(plain_name)
    try:
        with os.fdopen(fd, "w+b") as plain_fh:
            with path.open("rb") as src:
                decrypt_stream(src, plain_fh, master_keys(settings))
            plain_fh.seek(0)
            with zipfile.ZipFile(plain_fh) as zf:
                try:
                    manifest = json.loads(zf.read("manifest.json"))
                except (KeyError, ValueError):
                    raise BackupError("The backup has no valid manifest.") from None
                if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
                    raise BackupError("That file isn't a Waypoint backup.")
                yield zf, manifest
    finally:
        plain.unlink(missing_ok=True)


async def restore_backup(
    sessionmaker: async_sessionmaker[AsyncSession], settings: Settings, path: Path
) -> dict[str, int]:
    """Load a backup into an empty database that's migrated to the same version."""
    with opened(path, settings) as (zf, manifest):
        async with sessionmaker() as db:
            revision = str(await db.scalar(text("SELECT version_num FROM alembic_version")) or "")
            if revision != manifest.get("schema_revision"):
                raise BackupError(
                    f"The backup is from database version {manifest.get('schema_revision')!r} "
                    f"but this database is at {revision!r}. Restore with the same Waypoint "
                    "version that made the backup."
                )
            if await db.scalar(select(exists().select_from(User))):
                raise BackupError("The database isn't empty. Restore into a fresh database.")
            counts: dict[str, int] = {}
            names = set(zf.namelist())
            for table in backup_tables():
                member = f"tables/{table.name}.jsonl"
                if member not in names:
                    counts[table.name] = 0
                    continue
                counts[table.name] = await _load_table(db, zf, member, table)
                if "id" in table.c and isinstance(table.c.id.type, Integer):
                    top = func.max(table.c.id)
                    await db.execute(
                        select(
                            func.setval(
                                func.pg_get_serial_sequence(table.name, "id"),
                                func.coalesce(top, 1),
                                top.is_not(None),
                            )
                        )
                    )
            await db.commit()
    return counts


def _self_reference(table: Table) -> tuple[str, str] | None:
    """(child column, parent column) when rows point at other rows of the same table."""
    for fk in table.foreign_key_constraints:
        if fk.referred_table is table:
            pairs = [
                (el.parent.name, el.column.name)
                for el in fk.elements
                if el.parent.name != "user_id"
            ]
            if len(pairs) == 1:
                return pairs[0]
    return None


def _parents_first(rows: list[dict[str, Any]], child: str, parent: str) -> list[dict[str, Any]]:
    by_key = {r.get(parent): r for r in rows}
    done: set[int] = set()
    ordered: list[dict[str, Any]] = []
    for row in rows:
        chain: list[dict[str, Any]] = []
        current: dict[str, Any] | None = row
        # Walk up to the first ancestor already placed (or the root), then place downwards.
        while current is not None and id(current) not in done and len(chain) <= len(rows):
            chain.append(current)
            current = by_key.get(current.get(child))
        for r in reversed(chain):
            if id(r) not in done:
                done.add(id(r))
                ordered.append(r)
    return ordered


async def _load_table(db: AsyncSession, zf: zipfile.ZipFile, member: str, table: Table) -> int:
    columns = {c.name: c for c in table.columns}
    rows: list[dict[str, Any]] = []
    with zf.open(member) as fh:
        for n, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                if not isinstance(raw, dict) or set(raw) - set(columns):
                    raise DecodeError("unexpected fields")
                rows.append({k: decode(columns[k], v) for k, v in raw.items()})
            except (ValueError, DecodeError) as exc:
                raise BackupError(f"{table.name} row {n} is invalid: {exc}") from None
    link = _self_reference(table)
    if link is not None:
        rows = _parents_first(rows, *link)
    for i in range(0, len(rows), 500):
        await db.execute(insert(table), rows[i : i + 500])
    return len(rows)
