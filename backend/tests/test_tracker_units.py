# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest
from resume_fixtures import make_docx, make_pdf, make_zip

from waypoint.security.crypto import CryptoError, FieldCipher, generate_key
from waypoint.tracker.files import content_disposition, sniff_attachment
from waypoint.tracker.schemas import ApplicationIn, OfferIn, annual_cents, hourly_cents
from waypoint.vault.importer.guards import DOCX_MIME, RejectedFile


def test_pay_maths() -> None:
    assert annual_cents(5_000, "hour", 40) == 5_000 * 40 * 52  # $50/h -> $104,000
    assert annual_cents(10_400_000, "year", 40) == 10_400_000
    assert hourly_cents(10_400_000, "year", 40) == 5_000
    assert hourly_cents(10_000_000, "year", 40) == 4_808  # 48.0769... rounds to 48.08
    assert hourly_cents(10_000_000, "year", 32) == 6_010
    assert hourly_cents(5_000, "hour", 20) == 5_000


def test_application_pay_validation() -> None:
    with pytest.raises(ValueError, match="above"):
        ApplicationIn(title="x", pay_min_cents=200, pay_max_cents=100, pay_period="hour")
    with pytest.raises(ValueError, match="pay_period"):
        ApplicationIn(title="x", pay_min_cents=100)
    with pytest.raises(ValueError):
        ApplicationIn(title="x", currency="usd")
    assert ApplicationIn(title="x").stage == "saved"


def test_offer_validation() -> None:
    with pytest.raises(ValueError):
        OfferIn(base_cents=0, base_period="year")
    with pytest.raises(ValueError):
        OfferIn(base_cents=100, base_period="year", hours_per_week=0)
    assert OfferIn(base_cents=100, base_period="hour").hours_per_week == 40


def test_binary_cipher_roundtrip_and_context() -> None:
    cipher = FieldCipher(f"1:{generate_key()}")
    blob = cipher.encrypt_bytes(b"\x00\x01 offer letter", "user:a:attachment:1")
    assert blob.startswith(b"v1:")
    assert b"offer letter" not in blob
    assert cipher.decrypt_bytes(blob, "user:a:attachment:1") == b"\x00\x01 offer letter"
    with pytest.raises(CryptoError):
        cipher.decrypt_bytes(blob, "user:b:attachment:1")
    with pytest.raises(CryptoError):
        cipher.decrypt_bytes(b"garbage", "x")
    other_key = FieldCipher(f"1:{generate_key()}")
    with pytest.raises(CryptoError):
        other_key.decrypt_bytes(blob, "user:a:attachment:1")


@pytest.mark.parametrize(
    ("data", "mime", "ext"),
    [
        (make_pdf(["x"]), "application/pdf", "pdf"),
        (make_docx(), DOCX_MIME, "docx"),
        (b"\x89PNG\r\n\x1a\n" + b"\0" * 20, "image/png", "png"),
        (b"\xff\xd8\xff\xe0" + b"\0" * 20, "image/jpeg", "jpg"),
        ("Notes from the call — café".encode(), "text/plain; charset=utf-8", "txt"),
    ],
)
def test_sniff_allows_known_types(data: bytes, mime: str, ext: str) -> None:
    assert sniff_attachment(data, mime) == ext


@pytest.mark.parametrize(
    ("data", "mime"),
    [
        (b"<html><script>alert(1)</script>", "text/html"),
        (b"<svg onload=alert(1)>", "image/svg+xml"),
        (b"<html><script>alert(1)</script>", "image/png"),  # declared type lies
        (make_pdf(["x"]), "image/png"),
        (b"MZ\x90\x00", "application/octet-stream"),
        (b"text with a \x00 byte", "text/plain"),
        (b"\xff\xfe not utf-8 \xff", "text/plain"),
        (make_zip({"xl/workbook.xml": b"x"}), DOCX_MIME),
    ],
)
def test_sniff_rejects_everything_else(data: bytes, mime: str) -> None:
    with pytest.raises(RejectedFile):
        sniff_attachment(data, mime)


def test_content_disposition_is_safe() -> None:
    header = content_disposition('offer "final".pdf')
    assert header.startswith('attachment; filename="offer _final_.pdf"')
    assert "filename*=UTF-8''offer%20%22final%22.pdf" in header
    assert "\n" not in content_disposition("a\r\nSet-Cookie: x.pdf")
