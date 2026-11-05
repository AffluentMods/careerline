# SPDX-License-Identifier: Apache-2.0
import time

import pyotp
import pytest

from waypoint.security import passwords, totp
from waypoint.security.crypto import CryptoError, FieldCipher, generate_key


def test_cipher_round_trip_and_context_binding() -> None:
    cipher = FieldCipher(f"1:{generate_key()}")
    token = cipher.encrypt("s3cret", "user:a:mfa")
    assert token.startswith("v1:")
    assert "s3cret" not in token
    assert cipher.decrypt(token, "user:a:mfa") == "s3cret"
    with pytest.raises(CryptoError):
        cipher.decrypt(token, "user:b:mfa")


def test_cipher_detects_tampering() -> None:
    cipher = FieldCipher(f"1:{generate_key()}")
    token = cipher.encrypt("value", "ctx")
    tampered = token[:-2] + ("A" if token[-2] != "A" else "B") + token[-1]
    with pytest.raises(CryptoError):
        cipher.decrypt(tampered, "ctx")


def test_cipher_key_rotation() -> None:
    old, new = generate_key(), generate_key()
    token = FieldCipher(f"1:{old}").encrypt("value", "ctx")
    rotated = FieldCipher(f"2:{new},1:{old}")
    assert rotated.decrypt(token, "ctx") == "value"
    assert rotated.encrypt("value", "ctx").startswith("v2:")


@pytest.mark.parametrize("bad", ["", "nokey", "1:short", "1:" + "A" * 10])
def test_cipher_rejects_bad_keyrings(bad: str) -> None:
    with pytest.raises(CryptoError):
        FieldCipher(bad)


def test_totp_accepts_current_and_rejects_replay() -> None:
    secret = totp.new_secret()
    now = time.time()
    code = pyotp.TOTP(secret).at(now)
    step = totp.verify(secret, code, None, now=now)
    assert step is not None
    assert totp.verify(secret, code, step, now=now) is None


def test_totp_window() -> None:
    secret = totp.new_secret()
    now = 1_800_000_000.0
    prev = pyotp.TOTP(secret).at(now - 30)
    old = pyotp.TOTP(secret).at(now - 90)
    assert totp.verify(secret, prev, None, now=now) is not None
    assert totp.verify(secret, old, None, now=now) is None


@pytest.mark.parametrize("code", ["", "12345", "abcdef", "1234567"])
def test_totp_rejects_malformed(code: str) -> None:
    assert totp.verify(totp.new_secret(), code, None) is None


def test_recovery_codes() -> None:
    codes = totp.new_recovery_codes()
    assert len(codes) == 10
    assert len(set(codes)) == 10
    for c in codes:
        assert totp.looks_like_recovery_code(c)
        assert totp.hash_recovery_code(c) == totp.hash_recovery_code(c.upper().replace("-", ""))


def test_password_policy() -> None:
    assert passwords.password_problem("short", "a@b.co")
    assert passwords.password_problem("aaaaaaaaaaaaaaa", "a@b.co")
    assert passwords.password_problem("someone@example.com", "someone@example.com")
    assert passwords.password_problem("x" * 300, "a@b.co")
    assert passwords.password_problem("correct horse battery", "a@b.co") is None


async def test_password_hash_is_argon2id() -> None:
    h = await passwords.hash_password("correct horse battery")
    assert h.startswith("$argon2id$")
    assert await passwords.verify_password(h, "correct horse battery")
    assert not await passwords.verify_password(h, "wrong password!!")
    assert not await passwords.verify_password(None, "anything at all")
