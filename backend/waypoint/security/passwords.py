# SPDX-License-Identifier: Apache-2.0
"""argon2id password hashing.

Hashing is CPU and memory heavy, so it runs in a worker thread to avoid
blocking the event loop.
"""

from __future__ import annotations

from anyio import to_thread
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# argon2-cffi defaults follow RFC 9106's low-memory profile:
# argon2id, t=3, m=64 MiB, p=4. That is above the OWASP minimum.
_hasher = PasswordHasher()

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256

# A real hash of a random value. Used for unknown accounts, so that the
# response time doesn't reveal whether an email address is registered.
_DUMMY_HASH = _hasher.hash("waypoint-dummy-password-for-timing")


async def hash_password(password: str) -> str:
    return await to_thread.run_sync(_hasher.hash, password)


def _verify(stored_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


async def verify_password(stored_hash: str | None, password: str) -> bool:
    if stored_hash is None:
        await to_thread.run_sync(_verify, _DUMMY_HASH, password)
        return False
    return await to_thread.run_sync(_verify, stored_hash, password)


def needs_rehash(stored_hash: str) -> bool:
    return _hasher.check_needs_rehash(stored_hash)


def password_problem(password: str, email: str) -> str | None:
    """Return a human-readable reason the password is unacceptable, or None."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Password must be at most {MAX_PASSWORD_LENGTH} characters."
    if password.strip().lower() == email.strip().lower():
        return "Password must not be your email address."
    if len(set(password)) < 4:
        return "Password is too repetitive."
    return None
