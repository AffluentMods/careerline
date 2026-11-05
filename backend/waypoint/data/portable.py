# SPDX-License-Identifier: Apache-2.0
"""Full export of one user's data (JSON + files in a zip) and importing it into an account.

Exports are portable between servers: encrypted fields (contact details, attachments) are
decrypted into the archive and re-encrypted with the importing server's keys. Secrets are
never exported (AI API keys, job-source keys, notification addresses and tokens), so they
have to be entered again after an import.

Imports treat the archive as untrusted: only known tables and columns, values checked
against each column's type, sizes capped, everything owned by the importing user, and every
record given a fresh id (references inside JSON, such as the vault facts a resume cites,
are rewritten to match). The import runs in one transaction: all or nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
import zipfile
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.exceptions import RequestValidationError
from sqlalchemy import Integer, Table, delete, exists, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from waypoint import __version__, model_registry
from waypoint.config import Settings
from waypoint.data.codec import DecodeError, decode, encode
from waypoint.db import Base
from waypoint.models import User
from waypoint.security import contexts
from waypoint.security.crypto import CryptoError, FieldCipher
from waypoint.vault.schemas import DATA_MODELS, parse_data

_ = model_registry  # imported for its side effect: every table is registered

FORMAT = "waypoint-export"
FORMAT_VERSION = 1

EXPORT_TABLES = frozenset(
    {
        "vault_profiles",
        "vault_entries",
        "stories",
        "story_entries",
        "applications",
        "application_events",
        "contacts",
        "application_contacts",
        "referrals",
        "reminders",
        "offers",
        "attachments",
        "job_sources",
        "job_filters",
        "job_postings",
        "posting_sightings",
        "job_scores",
        "documents",
        "ai_settings",
        "conversations",
        "chat_messages",
        "pending_actions",
        "interview_preps",
        "mock_sessions",
        "notification_settings",
    }
)
# Never leave the server.
SECRET_COLUMNS = frozenset(
    {
        ("ai_settings", "api_key_enc"),
        ("job_sources", "secret_enc"),
        ("notification_settings", "ntfy_url_enc"),
        ("notification_settings", "ntfy_token_enc"),
        ("notification_settings", "webhook_url_enc"),
    }
)
Context = Callable[[uuid.UUID, dict[str, Any]], str]
# (table, column) -> (name in the archive, encryption context)
ENCRYPTED: dict[tuple[str, str], tuple[str, Context]] = {
    ("vault_profiles", "email_enc"): ("email", lambda u, r: contexts.profile(u, "email")),
    ("vault_profiles", "phone_enc"): ("phone", lambda u, r: contexts.profile(u, "phone")),
    ("vault_profiles", "address_enc"): ("address", lambda u, r: contexts.profile(u, "address")),
    ("contacts", "email_enc"): ("email", lambda u, r: contexts.contact(u, r["id"], "email")),
    ("contacts", "phone_enc"): ("phone", lambda u, r: contexts.contact(u, r["id"], "phone")),
    ("notification_settings", "email_to_enc"): (
        "email_to",
        lambda u, r: contexts.notify(u, "email_to"),
    ),
}
FILE_COLUMN = ("attachments", "content_enc")

_TABLE_FILE = re.compile(r"^tables/([a-z_]{1,40})\.json$")
_FILE_NAME = re.compile(r"^files/([0-9a-f-]{36})$")
MAX_TABLE_BYTES = 200_000_000
# Everything in the archive, uncompressed. Bounds memory whatever the archive claims.
MAX_TOTAL_BYTES = 1_000_000_000
MAX_MEMBERS = 100_000


class ImportProblem(Exception):
    pass


def discard(path: Path) -> None:
    path.unlink(missing_ok=True)


def export_tables() -> list[Table]:
    return [t for t in Base.metadata.sorted_tables if t.name in EXPORT_TABLES]


def _singleton(table: Table) -> bool:
    return [c.name for c in table.primary_key.columns] == ["user_id"]


def _int_id(table: Table) -> bool:
    return "id" in table.c and isinstance(table.c.id.type, Integer)


async def schema_revision(db: AsyncSession) -> str:
    return str(await db.scalar(text("SELECT version_num FROM alembic_version")) or "")


# --- export -------------------------------------------------------------------------------------


async def export_user(
    db: AsyncSession, cipher: FieldCipher, user: User
) -> tuple[Path, dict[str, int]]:
    """Write the archive to a temporary file. The caller deletes it."""
    uid = user.id
    fd, name = tempfile.mkstemp(prefix="waypoint-export-", suffix=".zip")
    handle = os.fdopen(fd, "w+b")
    path = Path(name)
    counts: dict[str, int] = {}
    try:
        with handle, zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for table in export_tables():
                rows: list[dict[str, Any]] = []
                result = await db.stream(
                    select(table).where(table.c.user_id == uid).execution_options(yield_per=200)
                )
                async for row in result:
                    data = dict(row._mapping)
                    out: dict[str, Any] = {}
                    for col in table.columns:
                        key = (table.name, col.name)
                        value = data[col.name]
                        if key in SECRET_COLUMNS or col.name == "user_id":
                            continue
                        if key == FILE_COLUMN:
                            zf.writestr(
                                f"files/{data['id']}",
                                cipher.decrypt_bytes(value, contexts.attachment(uid, data["id"])),
                            )
                            continue
                        if key in ENCRYPTED:
                            alias, ctx = ENCRYPTED[key]
                            out[alias] = cipher.decrypt(value, ctx(uid, data)) if value else None
                            continue
                        out[col.name] = encode(value)
                    rows.append(out)
                counts[table.name] = len(rows)
                zf.writestr(f"tables/{table.name}.json", json.dumps(rows, ensure_ascii=False))
            manifest = {
                "format": FORMAT,
                "version": FORMAT_VERSION,
                "app_version": __version__,
                "schema_revision": await schema_revision(db),
                "exported_at": datetime.now(UTC).isoformat(),
                "user_id": str(uid),
                "user": {"email": user.email, "display_name": user.display_name},
                "counts": counts,
                "not_included": [
                    "AI provider API key",
                    "job source API keys",
                    "ntfy and webhook addresses",
                    "password, two-factor and sign-in settings",
                ],
            }
            zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
    except BaseException:
        discard(path)
        raise
    return path, counts


# --- import -------------------------------------------------------------------------------------


def _read_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    if info.file_size > limit:
        raise ImportProblem(f"{info.filename} is too large.")
    with zf.open(info) as fh:
        data = fh.read(limit + 1)
    if len(data) > limit:
        raise ImportProblem(f"{info.filename} is too large.")
    return data


def _remap_json(value: Any, ids: dict[str, str]) -> Any:
    if isinstance(value, str):
        return ids.get(value, value)
    if isinstance(value, list):
        return [_remap_json(v, ids) for v in value]
    if isinstance(value, dict):
        return {ids.get(k, k): _remap_json(v, ids) for k, v in value.items()}
    return value


def _parents_first(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """vault_entries reference their parent entry, so parents must be inserted first."""
    by_id = {r.get("id"): r for r in rows}
    done: set[Any] = set()
    ordered: list[dict[str, Any]] = []

    def visit(r: dict[str, Any], depth: int = 0) -> None:
        if r.get("id") in done or depth > 50:
            return
        parent = by_id.get(r.get("parent_id"))
        if parent is not None and parent is not r:
            visit(parent, depth + 1)
        done.add(r.get("id"))
        ordered.append(r)

    for r in rows:
        visit(r)
    return ordered


async def account_is_empty(db: AsyncSession, user_id: uuid.UUID) -> bool:
    for table in export_tables():
        if _singleton(table):
            continue
        if await db.scalar(select(exists().where(table.c.user_id == user_id))):
            return False
    return True


async def import_user(
    db: AsyncSession, cipher: FieldCipher, settings: Settings, user_id: uuid.UUID, archive: Path
) -> dict[str, Any]:
    """Load an export into the (empty) account. Caller commits; on error, rolls back."""
    try:
        zf = zipfile.ZipFile(archive)
    except (zipfile.BadZipFile, OSError):
        raise ImportProblem("That file isn't a Waypoint export (.zip).") from None
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_MEMBERS:
            raise ImportProblem("The archive has too many files.")
        if sum(i.file_size for i in infos) > MAX_TOTAL_BYTES:
            raise ImportProblem("The archive is too large once unpacked.")
        members = {i.filename: i for i in infos}
        if "manifest.json" not in members:
            raise ImportProblem("That file isn't a Waypoint export (no manifest).")
        try:
            manifest = json.loads(_read_member(zf, members["manifest.json"], 1_000_000))
        except (ValueError, RecursionError):
            raise ImportProblem("The export's manifest is damaged.") from None
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
            raise ImportProblem("That file isn't a Waypoint export.")
        if manifest.get("version") != FORMAT_VERSION:
            raise ImportProblem("This export was made by a newer version of Waypoint.")
        if not await account_is_empty(db, user_id):
            raise ImportProblem(
                "Imports go into an empty account. Create a new account for this data, "
                "or import before adding anything."
            )

        tables = {t.name: t for t in export_tables()}
        raw: dict[str, list[dict[str, Any]]] = {}
        for name, info in members.items():
            m = _TABLE_FILE.match(name)
            if not m:
                continue
            if m.group(1) not in tables:
                raise ImportProblem(f"The export has an unknown table ({m.group(1)}).")
            try:
                rows = json.loads(_read_member(zf, info, MAX_TABLE_BYTES))
            except (ValueError, RecursionError):
                raise ImportProblem(f"{name} is damaged.") from None
            if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
                raise ImportProblem(f"{name} isn't a list of records.")
            raw[m.group(1)] = rows

        # Fresh ids for every record; references to the old ones are rewritten.
        ids: dict[str, str] = {}
        old_user = manifest.get("user_id")
        if isinstance(old_user, str):
            ids[old_user] = str(user_id)
        for name, rows in raw.items():
            table = tables[name]
            if "id" in table.c and not _int_id(table):
                for r in rows:
                    old = r.get("id")
                    if not isinstance(old, str) or old in ids:
                        raise ImportProblem(f"{name} has a record with a missing or repeated id.")
                    ids[old] = str(uuid.uuid4())

        counts: dict[str, int] = {}
        notes: list[str] = []
        file_bytes = 0
        for table in export_tables():
            rows = raw.get(table.name, [])
            if table.name == "vault_entries":
                rows = _parents_first(rows)
            if _singleton(table):
                await db.execute(delete(table).where(table.c.user_id == user_id))
            prepared: list[dict[str, Any]] = []
            for n, r in enumerate(rows, start=1):
                try:
                    values, size = _prepare(table, r, ids, user_id, cipher, zf, members)
                except (DecodeError, CryptoError, RecursionError) as exc:
                    raise ImportProblem(f"{table.name} record {n}: {exc}") from None
                file_bytes += size
                if file_bytes > settings.attachment_quota_bytes:
                    raise ImportProblem("The attachments are larger than your storage allowance.")
                prepared.append(values)
            if table.name == "job_sources" and prepared:
                notes.append(
                    "Job sources were imported turned off. Re-enter any API keys, "
                    "then turn them on."
                )
            if table.name == "ai_settings" and prepared:
                notes.append("Re-enter your AI provider's API key in Settings \u2192 AI.")
            await _insert(db, table, prepared)
            counts[table.name] = len(prepared)
        return {"imported": counts, "notes": notes, "from_version": manifest.get("app_version")}


def _prepare(
    table: Table,
    row: dict[str, Any],
    ids: dict[str, str],
    user_id: uuid.UUID,
    cipher: FieldCipher,
    zf: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
) -> tuple[dict[str, Any], int]:
    aliases = {alias: col for (t, col), (alias, _) in ENCRYPTED.items() if t == table.name}
    known = {c.name for c in table.columns} | set(aliases)
    unknown = set(row) - known
    if unknown:
        raise DecodeError(
            f"unknown field {sorted(unknown)[0]!r} (made by a newer version of Waypoint?)"
        )
    values: dict[str, Any] = {}
    for col in table.columns:
        key = (table.name, col.name)
        if col.name == "user_id":
            values["user_id"] = user_id
        elif key in SECRET_COLUMNS or key in ENCRYPTED or key == FILE_COLUMN:
            continue
        elif col.name == "id" and _int_id(table):
            continue  # the database numbers these
        elif col.name in row:
            value = row[col.name]
            if _is_uuid(col):
                if value is not None:
                    if not isinstance(value, str):
                        raise DecodeError(f"{col.name} must be an id")
                    if value in ids:
                        value = ids[value]
                    elif col.foreign_keys or col.primary_key:
                        raise DecodeError(f"{col.name} points to a record that isn't in the export")
                    else:
                        value = None  # a loose reference to something long gone
            elif _is_json(col):
                value = _remap_json(value, ids)
            values[col.name] = decode(col, value)
    for (t, col_name), (alias, ctx) in ENCRYPTED.items():
        if t != table.name:
            continue
        plain = row.get(alias)
        if plain is not None and not isinstance(plain, str):
            raise DecodeError(f"{alias} must be text")
        values[col_name] = cipher.encrypt(plain, ctx(user_id, values)) if plain else None
    size = 0
    if table.name == FILE_COLUMN[0]:
        info = members.get(f"files/{row.get('id')}")
        if info is None:
            raise DecodeError("the attachment's file is missing")
        data = _read_member(zf, info, 50_000_000)
        size = len(data)
        values["content_enc"] = cipher.encrypt_bytes(
            data, contexts.attachment(user_id, values["id"])
        )
        # Recomputed: never trust the archive's own claims about the file.
        values["size_bytes"] = size
        values["sha256"] = hashlib.sha256(data).hexdigest()
    if table.name == "job_sources":
        values["enabled"] = False
    if table.name == "pending_actions" and values.get("status") == "pending":
        # Never carry an unconfirmed assistant action into another account.
        values["status"] = "rejected"
        values["result"] = "Not carried over by the import."
    if table.name == "vault_entries":
        # The same validation the vault API applies: an archive can't plant odd data.
        kind = values.get("kind")
        if kind not in DATA_MODELS:
            raise DecodeError("kind isn't a vault entry type")
        try:
            values["data"] = parse_data(str(kind), values.get("data"))
        except RequestValidationError:
            raise DecodeError(f"data isn't valid for a {kind} entry") from None
    for col in table.columns:
        # Links are shown as links: only plain web addresses get in.
        value = values.get(col.name)
        if (
            col.name.endswith("url")
            and isinstance(value, str)
            and value
            and not value.lower().startswith(("https://", "http://"))
        ):
            raise DecodeError(f"{col.name} must be a web address (http or https)")
    return values, size


def _is_uuid(col: Any) -> bool:
    try:
        return col.type.python_type is uuid.UUID
    except NotImplementedError:
        return False


def _is_json(col: Any) -> bool:
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.types import JSON

    return isinstance(col.type, JSON | JSONB)


async def _insert(db: AsyncSession, table: Table, rows: list[dict[str, Any]]) -> None:
    """Insert in batches; rows with the same set of keys go together (executemany)."""
    groups: dict[frozenset[str], list[dict[str, Any]]] = defaultdict(list)
    order: list[frozenset[str]] = []
    if table.name == "vault_entries":
        # Keep parent-before-child order: one row at a time.
        for r in rows:
            await db.execute(insert(table).values(**r))
        return
    for r in rows:
        k = frozenset(r)
        if k not in groups:
            order.append(k)
        groups[k].append(r)
    for k in order:
        batch = groups[k]
        for i in range(0, len(batch), 500):
            await db.execute(insert(table), batch[i : i + 500])
