# SPDX-License-Identifier: Apache-2.0
"""TOTP (RFC 6238) and recovery codes."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

import pyotp

ISSUER = "Waypoint"
DIGITS = 6
INTERVAL = 30
# Accept the previous, current, and next 30-second step to allow for clock drift.
WINDOW = 1

RECOVERY_CODE_COUNT = 10
_RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o/1/l/i


def new_secret() -> str:
    return pyotp.random_base32(length=32)


def provisioning_uri(secret: str, account: str) -> str:
    return pyotp.TOTP(secret, digits=DIGITS, interval=INTERVAL).provisioning_uri(
        name=account, issuer_name=ISSUER
    )


def verify(secret: str, code: str, last_step: int | None, now: float | None = None) -> int | None:
    """Return the matched time step, or None.

    A step at or before ``last_step`` is rejected so each code works only once.
    """
    code = code.strip().replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return None
    totp = pyotp.TOTP(secret, digits=DIGITS, interval=INTERVAL)
    current = int((time.time() if now is None else now) // INTERVAL)
    matched: int | None = None
    for step in range(current - WINDOW, current + WINDOW + 1):
        # Check every candidate so timing does not depend on which one matched.
        if hmac.compare_digest(totp.generate_otp(step), code) and matched is None:
            matched = step
    if matched is None or (last_step is not None and matched <= last_step):
        return None
    return matched


def new_recovery_codes() -> list[str]:
    def one() -> str:
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(12))
        return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"

    return [one() for _ in range(RECOVERY_CODE_COUNT)]


def normalise_recovery_code(code: str) -> str:
    return "".join(ch for ch in code.lower() if ch.isalnum())


def hash_recovery_code(code: str) -> bytes:
    return hashlib.sha256(normalise_recovery_code(code).encode()).digest()


def looks_like_recovery_code(code: str) -> bool:
    return len(normalise_recovery_code(code)) == 12
