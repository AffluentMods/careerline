# SPDX-License-Identifier: Apache-2.0
"""ASGI middleware: security headers and request size limits."""

from __future__ import annotations

import re
from collections.abc import Sequence

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# "sandbox" matters for attachment downloads: even if a browser rendered one, it
# would get an opaque origin with no scripts.
API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; sandbox"
APP_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'"
)
# Swagger UI (development only) loads its assets from a CDN and uses inline script.
DOCS_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: "
    "https://fastapi.tiangolo.com; frame-ancestors 'none'"
)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, hsts: bool, docs_enabled: bool) -> None:
        self.app = app
        self.hsts = hsts
        self.docs_enabled = docs_enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]
        is_api = path.startswith("/api/")
        is_docs = self.docs_enabled and path.startswith(("/api/docs", "/api/openapi.json"))

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                h = MutableHeaders(scope=message)
                if is_docs:
                    h["Content-Security-Policy"] = DOCS_CSP
                else:
                    h["Content-Security-Policy"] = API_CSP if is_api else APP_CSP
                h["X-Content-Type-Options"] = "nosniff"
                h["X-Frame-Options"] = "DENY"
                h["Referrer-Policy"] = "no-referrer"
                h["Cross-Origin-Opener-Policy"] = "same-origin"
                h["Cross-Origin-Resource-Policy"] = "same-origin"
                h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
                if is_api:
                    h["Cache-Control"] = "no-store"
                if self.hsts:
                    h["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
            await send(message)

        await self.app(scope, receive, send_with_headers)


class BodySizeLimitMiddleware:
    """Reject request bodies over ``max_bytes``.

    ``overrides`` is a list of ``(method, path regex, limit)``: upload routes can
    accept more without raising the limit everywhere. The regex must match the
    whole path.
    A declared Content-Length is checked up front. Bodies without one
    (chunked) are counted while streaming and cut off at the limit.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_bytes: int,
        overrides: Sequence[tuple[str, str, int]] = (),
    ) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.overrides = [(m, re.compile(p), limit) for m, p, limit in overrides]

    def _limit(self, method: str, path: str) -> int:
        for m, pattern, limit in self.overrides:
            if m == method and pattern.fullmatch(path):
                return limit
        return self.max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self._limit(scope["method"], scope["path"])
        declared = Headers(scope=scope).get("content-length")
        if declared is not None:
            if not declared.isdigit() or int(declared) > limit:
                await _too_large(scope, receive, send)
                return
            await self.app(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _BodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _BodyTooLarge:
            await _too_large(scope, receive, send)


class _BodyTooLarge(Exception):
    pass


async def _too_large(scope: Scope, receive: Receive, send: Send) -> None:
    response = JSONResponse(
        {"error": {"code": "payload_too_large", "message": "Request body is too large."}},
        status_code=413,
    )
    await response(scope, receive, send)
