# SPDX-License-Identifier: Apache-2.0
"""Delivery channels: email (SMTP), ntfy, and chat webhooks (Discord, Slack, generic JSON).

Notification text includes job titles and companies from postings, which are untrusted.
Every channel gets plain text with its own markup escaped, so a posting can't add links,
mentions (@everyone) or formatting to a message.
"""

from __future__ import annotations

import asyncio
import re
import smtplib
import ssl
from collections.abc import Callable
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from careerline.config import Settings
from careerline.net import FetchError, SafeFetcher

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_DISCORD_SPECIAL = re.compile(r"([\\*_~`|>\[\]()#<@!:-])")
_TOPIC = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_WEBHOOK_HOSTS = {
    "discord": ("discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com"),
    "slack": ("hooks.slack.com",),
}


class SendError(Exception):
    pass


@dataclass
class Message:
    kind: str  # digest | matches | test
    title: str
    lines: list[str] = field(default_factory=list)
    link: str = ""

    def plain(self) -> str:
        body = "\n".join(f"- {clean(line)}" for line in self.lines)
        tail = f"\n\nOpen CareerLine: {self.link}" if self.link else ""
        return f"{clean(self.title)}\n\n{body}{tail}".strip()


def clean(text: str, limit: int = 300) -> str:
    one_line = _CONTROL.sub(" ", text).replace("\n", " ").strip()
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "\u2026"


# --- URL checks (at save time) ------------------------------------------------------------


def check_ntfy_url(url: str) -> str:
    """``https://ntfy.sh/my-topic`` (or a self-hosted server, optionally under a path)."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise ValueError("Enter the full topic address, like https://ntfy.sh/your-topic.")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("Put the access token in its own field, not in the address.")
    topic = parts.path.rstrip("/").rsplit("/", 1)[-1]
    if not _TOPIC.match(topic):
        raise ValueError("The address must end with a topic name (letters, digits, - and _).")
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def check_webhook_url(kind: str, url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme != "https" and not (kind == "generic" and parts.scheme == "http"):
        raise ValueError("Webhook addresses must start with https://.")
    host = (parts.hostname or "").lower()
    if not host or parts.username or parts.password:
        raise ValueError("That isn't a plain web address.")
    allowed = _WEBHOOK_HOSTS.get(kind)
    if allowed and host not in allowed:
        raise ValueError(
            f"A {kind.capitalize()} webhook address starts with https://{allowed[0]}/."
        )
    return url.strip()


def mask_url(url: str) -> str:
    """Show where a secret URL points without revealing its token."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.hostname}/\u2026" if parts.hostname else ""


# --- payloads -------------------------------------------------------------------------------


def discord_payload(msg: Message) -> dict[str, Any]:
    def esc(s: str) -> str:
        return _DISCORD_SPECIAL.sub(r"\\\1", clean(s))

    lines = "\n".join(f"\u2022 {esc(line)}" for line in msg.lines)
    link = f"\n{msg.link}" if msg.link else ""
    content = f"**{esc(msg.title)}**\n{lines}"[: 1990 - len(link)] + link
    # No @everyone/@here/role pings, whatever the text says.
    return {"username": "CareerLine", "content": content, "allowed_mentions": {"parse": []}}


def slack_payload(msg: Message) -> dict[str, Any]:
    def esc(s: str) -> str:
        return clean(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    lines = "\n".join(f"\u2022 {esc(line)}" for line in msg.lines)
    link = f"\n<{msg.link}|Open CareerLine>" if msg.link else ""
    return {"text": f"*{esc(msg.title)}*\n{lines}{link}"[:3900], "unfurl_links": False}


def generic_payload(msg: Message) -> dict[str, Any]:
    return {
        "source": "careerline",
        "kind": msg.kind,
        "title": clean(msg.title),
        "items": [clean(line) for line in msg.lines],
        "link": msg.link,
    }


def ntfy_payload(url: str, msg: Message) -> tuple[str, dict[str, Any]]:
    """ntfy's JSON publishing: POST to the server root with the topic in the body."""
    parts = urlsplit(url)
    base_path, _, topic = parts.path.rstrip("/").rpartition("/")
    server = urlunsplit((parts.scheme, parts.netloc, base_path + "/", "", ""))
    body: dict[str, Any] = {
        "topic": topic,
        "title": clean(msg.title, 200),
        "message": "\n".join(clean(line) for line in msg.lines)[:3900] or clean(msg.title),
        "tags": ["briefcase"],
    }
    if msg.link:
        body["click"] = msg.link
    return server, body


# --- senders --------------------------------------------------------------------------------


async def _post(fetcher: SafeFetcher, url: str, payload: Any, headers: dict[str, str]) -> None:
    try:
        res = await fetcher.post(url, json=payload, headers=headers, max_redirects=0)
    except FetchError as exc:
        raise SendError(str(exc)) from None
    if not 200 <= res.status < 300:
        raise SendError(f"The server answered with status {res.status}.")


async def send_webhook(fetcher: SafeFetcher, kind: str, url: str, msg: Message) -> None:
    payload = {"discord": discord_payload, "slack": slack_payload}.get(kind, generic_payload)(msg)
    await _post(fetcher, url, payload, {"Content-Type": "application/json"})


async def send_ntfy(fetcher: SafeFetcher, url: str, token: str, msg: Message) -> None:
    server, body = ntfy_payload(url, msg)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    await _post(fetcher, server, body, headers)


EmailTransport = Callable[[Settings, EmailMessage], None]


def _smtp_send(settings: Settings, message: EmailMessage) -> None:
    context = ssl.create_default_context()
    password = settings.smtp_password.get_secret_value() if settings.smtp_password else ""
    smtp: smtplib.SMTP
    if settings.smtp_security == "tls":
        smtp = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20, context=context)
    else:
        smtp = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20)
    with smtp:
        if settings.smtp_security == "starttls":
            smtp.starttls(context=context)
        if settings.smtp_username:
            smtp.login(settings.smtp_username, password)
        smtp.send_message(message)


_email_transport: EmailTransport = _smtp_send


def set_email_transport(transport: EmailTransport | None) -> None:
    """Tests swap in a fake SMTP transport."""
    global _email_transport
    _email_transport = transport or _smtp_send


async def send_email(settings: Settings, to: str, msg: Message) -> None:
    if not settings.smtp_enabled:
        raise SendError("Email isn't set up on this server.")
    email = EmailMessage()
    email["From"] = settings.smtp_from
    email["To"] = to
    email["Subject"] = clean(msg.title, 150)
    email["Date"] = formatdate(localtime=False)
    email["Message-ID"] = make_msgid(domain=settings.smtp_from.rpartition("@")[2] or None)
    email.set_content(msg.plain() + "\n")
    try:
        await asyncio.wait_for(asyncio.to_thread(_email_transport, settings, email), timeout=60)
    except (smtplib.SMTPException, OSError, TimeoutError) as exc:
        raise SendError(f"The mail server refused the message ({type(exc).__name__}).") from None
