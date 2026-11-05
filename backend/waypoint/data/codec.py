# SPDX-License-Identifier: Apache-2.0
"""Turning database rows into JSON and back, driven by each column's SQL type.

Decoding is strict: archives are untrusted input, so every value must already have the
column's type (a string for a UUID or date, a number for an integer, and so on). JSON
columns take any JSON value; the database's own constraints check everything else.
"""

from __future__ import annotations

import base64
import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import Column, Table
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.types import JSON


class DecodeError(ValueError):
    pass


def encode(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes | memoryview):
        return {"$b64": base64.b64encode(bytes(value)).decode()}
    if isinstance(value, list | tuple):
        return [encode(v) for v in value]
    return value


def encode_row(table: Table, row: Any) -> dict[str, Any]:
    mapping = row._mapping if hasattr(row, "_mapping") else row
    return {c.name: encode(mapping[c.name]) for c in table.columns}


def _python_type(col: Column[Any]) -> type | None:
    try:
        pt: type = col.type.python_type
    except NotImplementedError:
        return None
    return pt


def decode(col: Column[Any], value: Any) -> Any:
    if value is None:
        if not col.nullable and col.server_default is None and col.default is None:
            raise DecodeError(f"{col.name} is required")
        return None
    t = col.type
    if isinstance(t, JSON | JSONB):
        return value
    if isinstance(t, ARRAY):
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise DecodeError(f"{col.name} must be a list of text")
        return value
    pt = _python_type(col)
    try:
        if pt is uuid.UUID:
            return uuid.UUID(_str(value, col.name))
        if pt is datetime:
            parsed = datetime.fromisoformat(_str(value, col.name))
            if getattr(t, "timezone", False) and parsed.tzinfo is None:
                raise DecodeError(f"{col.name} needs a time zone")
            return parsed
        if pt is date:
            return date.fromisoformat(_str(value, col.name))
        if pt is time:
            return time.fromisoformat(_str(value, col.name))
        if pt is bytes:
            if not isinstance(value, dict) or not isinstance(value.get("$b64"), str):
                raise DecodeError(f"{col.name} must be base64 data")
            return base64.b64decode(value["$b64"], validate=True)
        if pt is bool:
            if not isinstance(value, bool):
                raise DecodeError(f"{col.name} must be true or false")
            return value
        if pt is int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise DecodeError(f"{col.name} must be a whole number")
            return value
        if pt is float:
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise DecodeError(f"{col.name} must be a number")
            return float(value)
        if pt is Decimal:
            return Decimal(_str(value, col.name) if isinstance(value, str) else str(value))
        if pt is str:
            return _str(value, col.name)
    except DecodeError:
        raise
    except (ValueError, TypeError, ArithmeticError) as exc:
        raise DecodeError(f"{col.name} has an invalid value") from exc
    raise DecodeError(f"{col.name} has an unsupported type")


def _str(value: Any, name: str = "value") -> str:
    if not isinstance(value, str):
        raise DecodeError(f"{name} must be text")
    return value
