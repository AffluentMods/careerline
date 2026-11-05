# SPDX-License-Identifier: Apache-2.0
"""Audit log writer. Never put secrets, passwords, codes, or tokens in ``details``."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from careerline.models import AuditLog


def client_ip(request: Request) -> str | None:
    # Behind a reverse proxy, run uvicorn with --proxy-headers and
    # --forwarded-allow-ips so request.client reflects the real client.
    return request.client.host if request.client else None


def user_agent(request: Request) -> str | None:
    ua = request.headers.get("user-agent")
    return ua[:256] if ua else None


def record(
    db: AsyncSession,
    request: Request,
    event: str,
    user_id: uuid.UUID | None = None,
    **details: Any,
) -> None:
    """Add an audit entry to the session. The caller commits."""
    db.add(
        AuditLog(
            user_id=user_id,
            event=event,
            ip=client_ip(request),
            user_agent=user_agent(request),
            details=details,
        )
    )
