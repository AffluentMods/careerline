# SPDX-License-Identifier: Apache-2.0
"""Outbound HTTP for job sources, URL import, AI providers, and notifications.

Every request goes through :class:`SafeFetcher`:

* only ``http``/``https`` URLs, on ports 80/443 unless private targets are allowed;
* the host name is resolved once, **every** address must be public (no loopback,
  private, link-local, multicast, or reserved ranges), and the connection is
  pinned to the checked address, so DNS rebinding can't swap in an internal IP
  between the check and the connect (TLS still verifies the real host name);
* redirects are followed by hand (at most 3) and each hop is checked again;
* responses are streamed and cut off at a size limit, with short timeouts.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import urllib.robotparser
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from careerline import __version__

USER_AGENT = f"CareerLine/{__version__} (self-hosted job search assistant)"
ROBOTS_AGENT = "CareerLineBot"
DEFAULT_MAX_BYTES = 5_000_000
DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_REDIRECTS = (301, 302, 303, 307, 308)

Resolver = Callable[[str, int], Awaitable[list[str]]]


class FetchError(Exception):
    """A request we refused or that failed. ``message`` is safe to show users."""

    def __init__(self, code: str, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass
class FetchResult:
    url: str
    status: int
    headers: Mapping[str, str]
    body: bytes
    content_type: str = ""

    def text(self) -> str:
        charset = "utf-8"
        for part in self.content_type.split(";")[1:]:
            key, _, value = part.strip().partition("=")
            if key.lower() == "charset" and value:
                charset = value.strip("\"'")
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        import json

        try:
            return json.loads(self.body)
        except ValueError:
            raise FetchError(
                "bad_response", "The service sent back something unreadable."
            ) from None


_NAT64 = ipaddress.ip_network("64:ff9b::/96")  # RFC 6052 well-known prefix
_IPV4_COMPATIBLE = ipaddress.ip_network("::/96")  # deprecated IPv4-compatible form


def is_public_address(address: str) -> bool:
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip in _NAT64:
            # A NAT64 gateway turns this into the IPv4 address in its low 32 bits,
            # so judge that address (64:ff9b::a9fe:a9fe is 169.254.169.254).
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        elif ip in _IPV4_COMPATIBLE:
            return False
    return bool(ip.is_global) and not ip.is_multicast


async def system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise FetchError("dns_failed", f"Couldn't find the server {host!r}.") from None
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


@dataclass
class SafeFetcher:
    """See module docstring. Pass ``transport`` (and ``resolver``) in tests."""

    transport: httpx.AsyncBaseTransport | None = None
    resolver: Resolver = system_resolver
    allow_private: bool = False
    max_bytes: int = DEFAULT_MAX_BYTES
    timeout: httpx.Timeout = field(default_factory=lambda: DEFAULT_TIMEOUT)

    async def _target(self, url: str) -> tuple[str, str, int, str]:
        """Validate ``url``; return (scheme, host, port, pinned address)."""
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme not in {"http", "https"}:
            raise FetchError("bad_url", "Only http and https links are supported.")
        host = parts.hostname
        if not host or parts.username or parts.password:
            raise FetchError("bad_url", "That link isn't a plain web address.")
        port = parts.port or (443 if scheme == "https" else 80)
        if not self.allow_private and port not in (80, 443):
            raise FetchError("bad_url", "Only standard web ports (80 and 443) are allowed.")
        try:
            literal = ipaddress.ip_address(host)
            addresses = [str(literal)]
        except ValueError:
            addresses = await self.resolver(host, port)
        if not addresses:
            raise FetchError("dns_failed", f"Couldn't find the server {host!r}.")
        if not self.allow_private and not all(is_public_address(a) for a in addresses):
            raise FetchError(
                "blocked_address", "That address points to a private network and was blocked."
            )
        return scheme, host, port, addresses[0]

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        content: bytes | None = None,
        max_redirects: int = 3,
        max_bytes: int | None = None,
    ) -> FetchResult:
        limit = max_bytes or self.max_bytes
        current = str(httpx.URL(url, params=params)) if params else url
        async with httpx.AsyncClient(
            transport=self.transport, timeout=self.timeout, follow_redirects=False, trust_env=False
        ) as client:
            for _ in range(max_redirects + 1):
                scheme, host, port, address = await self._target(current)
                parts = urlsplit(current)
                ip_host = f"[{address}]" if ":" in address else address
                default_port = port == (443 if scheme == "https" else 80)
                netloc = ip_host if default_port else f"{ip_host}:{port}"
                pinned = urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))
                req_headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}
                req_headers.update(headers or {})
                req_headers["Host"] = host if default_port else f"{host}:{port}"
                extensions = {"sni_hostname": host} if scheme == "https" else {}
                try:
                    request = client.build_request(
                        method,
                        pinned,
                        headers=req_headers,
                        json=json,
                        content=content,
                        extensions=extensions,
                    )
                    response = await client.send(request, stream=True)
                except httpx.TimeoutException:
                    raise FetchError("timeout", "The server took too long to answer.") from None
                except httpx.HTTPError:
                    raise FetchError("connect_failed", f"Couldn't connect to {host}.") from None
                try:
                    if response.status_code in _REDIRECTS and "location" in response.headers:
                        current = urljoin(current, response.headers["location"])
                        method = "GET" if response.status_code == 303 else method
                        continue
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > limit:
                            raise FetchError("too_large", "The response was too large.")
                finally:
                    await response.aclose()
                return FetchResult(
                    url=current,
                    status=response.status_code,
                    headers=dict(response.headers),
                    body=bytes(body),
                    content_type=response.headers.get("content-type", ""),
                )
        raise FetchError("too_many_redirects", "The link redirected too many times.")

    async def get(self, url: str, **kw: Any) -> FetchResult:
        return await self.request("GET", url, **kw)

    async def post(self, url: str, **kw: Any) -> FetchResult:
        return await self.request("POST", url, **kw)


@dataclass
class OfflineFetcher(SafeFetcher):
    """Refuses every request. Used in demo mode, where nothing may leave the server."""

    async def request(self, method: str, url: str, **kw: Any) -> FetchResult:
        raise FetchError("offline", "Nothing is sent outside the server in demo mode.")


def fetcher_factory(settings: Any) -> Callable[[], SafeFetcher]:
    """The outbound HTTP client for user-configured addresses (feeds, webhooks, AI)."""
    if settings.demo_mode:
        return OfflineFetcher
    return lambda: SafeFetcher(allow_private=settings.outbound_allow_private)


async def robots_allows(fetcher: SafeFetcher, url: str, agent: str = ROBOTS_AGENT) -> bool:
    """RFC 9309: 4xx (or missing) robots.txt means allowed; 5xx or unreachable means disallowed."""
    parts = urlsplit(url)
    robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
    try:
        result = await fetcher.get(robots_url, max_bytes=500_000)
    except FetchError:
        return False
    if 400 <= result.status < 500:
        return True
    if result.status >= 500 or result.status >= 300:
        return False
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(result.text().splitlines())
    return parser.can_fetch(agent, url)
