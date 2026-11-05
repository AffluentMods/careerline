# SPDX-License-Identifier: Apache-2.0
"""Re-encrypt every encrypted field with the active key, so old keys can be retired.

Rotation, end to end:

1. Put a new key first in ``WAYPOINT_ENCRYPTION_KEYS`` (``2:<new>,1:<old>``). New writes
   use it at once, and older values still decrypt with the old key.
2. Run ``python -m waypoint.data rekey`` (or ``start.ps1 -RotateKey``, which also does
   step 1). Every value written with an older key is decrypted and written again with
   the new one.
3. ``python -m waypoint.data keys`` shows which key versions are still in use. When the
   old one shows zero, it can be removed from ``WAYPOINT_ENCRYPTION_KEYS``.

Each value is decrypted and re-encrypted with the same context it was written with, so the
binding to its row and column is kept. Updates are compare-and-swap on the old ciphertext
and committed in batches: the command can be stopped and run again, and it never
overwrites a value that changed meanwhile. Waypoint should be stopped while it runs: an
app still running with the old key list can't read values written with the new key.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Column, Table, func, literal, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from waypoint import model_registry
from waypoint.db import Base
from waypoint.models import AuditLog
from waypoint.security import contexts
from waypoint.security.crypto import CryptoError, FieldCipher

_ = model_registry  # imported for its side effect: every table is registered


class RekeyError(Exception):
    pass


Row = Mapping[str, Any]


@dataclass(frozen=True)
class EncryptedField:
    table: str
    column: str
    context: Callable[[Row], str]
    binary: bool = False

    @property
    def name(self) -> str:
        return f"{self.table}.{self.column}"


def _uid(r: Row) -> uuid.UUID:
    value: uuid.UUID = r["user_id"]
    return value


# Every encrypted column in the database. test_rekey checks that nothing is missing.
FIELDS: tuple[EncryptedField, ...] = (
    EncryptedField("users", "mfa_secret_enc", lambda r: contexts.mfa(r["id"])),
    EncryptedField(
        "users", "mfa_pending_secret_enc", lambda r: contexts.mfa(r["id"], pending=True)
    ),
    EncryptedField("vault_profiles", "email_enc", lambda r: contexts.profile(_uid(r), "email")),
    EncryptedField("vault_profiles", "phone_enc", lambda r: contexts.profile(_uid(r), "phone")),
    EncryptedField("vault_profiles", "address_enc", lambda r: contexts.profile(_uid(r), "address")),
    EncryptedField(
        "resume_imports", "text_enc", lambda r: contexts.resume_import(_uid(r), r["id"], "text")
    ),
    EncryptedField(
        "resume_imports",
        "suggestions_enc",
        lambda r: contexts.resume_import(_uid(r), r["id"], "s"),
    ),
    EncryptedField("contacts", "email_enc", lambda r: contexts.contact(_uid(r), r["id"], "email")),
    EncryptedField("contacts", "phone_enc", lambda r: contexts.contact(_uid(r), r["id"], "phone")),
    EncryptedField(
        "attachments",
        "content_enc",
        lambda r: contexts.attachment(_uid(r), r["id"]),
        binary=True,
    ),
    EncryptedField("job_sources", "secret_enc", lambda r: contexts.source_secret(_uid(r), r["id"])),
    EncryptedField("ai_settings", "api_key_enc", lambda r: contexts.ai_key(_uid(r))),
    EncryptedField(
        "notification_settings", "email_to_enc", lambda r: contexts.notify(_uid(r), "email_to")
    ),
    EncryptedField(
        "notification_settings", "ntfy_url_enc", lambda r: contexts.notify(_uid(r), "ntfy_url")
    ),
    EncryptedField(
        "notification_settings",
        "ntfy_token_enc",
        lambda r: contexts.notify(_uid(r), "ntfy_token"),
    ),
    EncryptedField(
        "notification_settings",
        "webhook_url_enc",
        lambda r: contexts.notify(_uid(r), "webhook_url"),
    ),
)


def _parts(field: EncryptedField) -> tuple[Table, Column[Any], Column[Any], list[Column[Any]]]:
    table = Base.metadata.tables[field.table]
    column = table.c[field.column]
    pk = next(iter(table.primary_key.columns))
    keys = [table.c[n] for n in ("id", "user_id") if n in table.c]
    return table, column, pk, [*{c.name: c for c in [pk, *keys]}.values(), column]


def _not_active(field: EncryptedField, column: Column[Any], active: str) -> Any:
    prefix = f"v{active}:"
    if field.binary:
        return func.substr(column, 1, len(prefix)) != literal(prefix.encode())
    return ~column.startswith(prefix, autoescape=True)


def _version_expr(field: EncryptedField, column: Column[Any]) -> Any:
    """SQL for the "v<version>" prefix of each stored value."""
    if field.binary:
        head = func.encode(func.substr(column, 1, 24), "escape")
        return func.split_part(head, ":", 1)
    return func.split_part(column, ":", 1)


async def key_usage(db: AsyncSession) -> dict[str, dict[str, int]]:
    """{field name: {key version: number of values}} for values that are set."""
    out: dict[str, dict[str, int]] = {}
    for field in FIELDS:
        _table, column, _, _ = _parts(field)
        version = _version_expr(field, column)
        rows = await db.execute(
            select(version, func.count()).where(column.is_not(None)).group_by(version)
        )
        counts = {str(v).removeprefix("v"): int(n) for v, n in rows.all()}
        if counts:
            out[field.name] = counts
    return out


def totals(usage: dict[str, dict[str, int]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for counts in usage.values():
        for version, n in counts.items():
            out[version] = out.get(version, 0) + n
    return dict(sorted(out.items()))


async def rekey(
    sessionmaker: async_sessionmaker[AsyncSession],
    cipher: FieldCipher,
    *,
    batch_size: int = 200,
) -> dict[str, int]:
    """Re-encrypt every value not on the active key. Returns {field name: values changed}."""
    active = cipher.active_version
    changed: dict[str, int] = {}
    for field in FIELDS:
        table, column, pk, selected = _parts(field)
        size = 10 if field.binary else batch_size
        last: Any = None
        done = 0
        async with sessionmaker() as db:
            while True:
                # Walk the table once in primary-key order, so the loop always ends.
                stmt = (
                    select(*selected)
                    .where(column.is_not(None), _not_active(field, column, active))
                    .order_by(pk)
                    .limit(size)
                )
                if last is not None:
                    stmt = stmt.where(pk > last)
                rows = (await db.execute(stmt)).mappings().all()
                if not rows:
                    break
                for row in rows:
                    old = row[column.name]
                    context = field.context(dict(row))
                    try:
                        if field.binary:
                            new: Any = cipher.encrypt_bytes(
                                cipher.decrypt_bytes(bytes(old), context), context
                            )
                        else:
                            new = cipher.encrypt(cipher.decrypt(old, context), context)
                    except CryptoError:
                        raise RekeyError(
                            f"{field.name} (row {row[pk.name]}) is encrypted with key "
                            f"{FieldCipher.version_of(old)!r}, which isn't in "
                            "WAYPOINT_ENCRYPTION_KEYS (or doesn't match it). Put that key back "
                            "after the first one and run this again. Nothing was lost."
                        ) from None
                    result = await db.execute(
                        update(table)
                        .where(pk == row[pk.name], column == old)
                        .values({column.name: new})
                    )
                    done += int(getattr(result, "rowcount", 0) or 0)
                last = rows[-1][pk.name]
                await db.commit()
        if done:
            changed[field.name] = done
    async with sessionmaker() as db:
        db.add(
            AuditLog(
                user_id=None,
                event="security.rekey",
                ip=None,
                user_agent="waypoint.data rekey",
                details={"active_version": active, "changed": sum(changed.values())},
            )
        )
        await db.commit()
    return changed
