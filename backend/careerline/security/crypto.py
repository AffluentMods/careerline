# SPDX-License-Identifier: Apache-2.0
"""Field-level encryption for sensitive columns (AES-256-GCM).

Ciphertext format: ``v<key-version>:<base64url(nonce || ciphertext+tag)>``.
Each value is bound to its context (for example ``user:<id>:mfa``) through
associated data, so a ciphertext copied into another row or column fails to
decrypt.
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_BYTES = 12


class CryptoError(Exception):
    pass


def _b64d(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except ValueError as exc:  # binascii.Error is a ValueError
        raise CryptoError("Invalid base64") from exc


def _b64e(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def generate_key() -> str:
    return _b64e(AESGCM.generate_key(bit_length=256))


def parse_keyring(keyring: str) -> list[tuple[str, bytes]]:
    """``"1:<key>,0:<old key>"`` -> [(version, 32 raw bytes), ...]; the first one encrypts."""
    out: list[tuple[str, bytes]] = []
    for raw_entry in keyring.split(","):
        entry = raw_entry.strip()
        if not entry:
            continue
        version, sep, key_b64 = entry.partition(":")
        if not sep or not version.isalnum():
            raise CryptoError("Encryption keys must look like '1:<base64url key>'")
        key = _b64d(key_b64)
        if len(key) != 32:
            raise CryptoError(f"Encryption key {version!r} must be 32 bytes")
        out.append((version, key))
    if not out:
        raise CryptoError("At least one encryption key is required")
    return out


def decode_key(value: str) -> bytes:
    key = _b64d(value.strip())
    if len(key) != 32:
        raise CryptoError("The key must be 32 bytes (base64url)")
    return key


class FieldCipher:
    def __init__(self, keyring: str) -> None:
        self._keys: dict[str, AESGCM] = {}
        self._active: str | None = None
        for version, key in parse_keyring(keyring):
            self._keys[version] = AESGCM(key)
            if self._active is None:
                self._active = version

    @property
    def active_version(self) -> str:
        """The key version new values are encrypted with (the first in the keyring)."""
        if self._active is None:  # pragma: no cover - guarded in __init__
            raise CryptoError("No active key")
        return self._active

    @property
    def versions(self) -> list[str]:
        return list(self._keys)

    @staticmethod
    def version_of(token: str | bytes) -> str:
        """The key version a stored value was encrypted with ('' if it isn't recognisable)."""
        raw = token if isinstance(token, bytes) else token.encode()
        prefix, sep, _ = raw[:20].partition(b":")
        return prefix[1:].decode("ascii", "replace") if sep and prefix.startswith(b"v") else ""

    def encrypt(self, plaintext: str, context: str) -> str:
        if self._active is None:  # pragma: no cover - guarded in __init__
            raise CryptoError("No active key")
        nonce = os.urandom(_NONCE_BYTES)
        ct = self._keys[self._active].encrypt(nonce, plaintext.encode(), context.encode())
        return f"v{self._active}:{_b64e(nonce + ct)}"

    def decrypt(self, token: str, context: str) -> str:
        prefix, sep, body = token.partition(":")
        if not sep or not prefix.startswith("v"):
            raise CryptoError("Malformed ciphertext")
        key = self._keys.get(prefix[1:])
        if key is None:
            raise CryptoError("Unknown encryption key version")
        raw = _b64d(body)
        try:
            return key.decrypt(raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], context.encode()).decode()
        except Exception as exc:  # InvalidTag and friends
            raise CryptoError("Decryption failed") from exc

    # Binary variant for files: ``b"v<version>:" + nonce + ciphertext+tag``, no base64.

    def encrypt_bytes(self, plaintext: bytes, context: str) -> bytes:
        if self._active is None:  # pragma: no cover - guarded in __init__
            raise CryptoError("No active key")
        nonce = os.urandom(_NONCE_BYTES)
        ct = self._keys[self._active].encrypt(nonce, plaintext, context.encode())
        return f"v{self._active}:".encode() + nonce + ct

    def decrypt_bytes(self, blob: bytes, context: str) -> bytes:
        prefix, sep, raw = blob.partition(b":")
        if not sep or not prefix.startswith(b"v") or len(prefix) > 16:
            raise CryptoError("Malformed ciphertext")
        key = self._keys.get(prefix[1:].decode("ascii", "replace"))
        if key is None:
            raise CryptoError("Unknown encryption key version")
        try:
            return key.decrypt(raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], context.encode())
        except Exception as exc:
            raise CryptoError("Decryption failed") from exc
