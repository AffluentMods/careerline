# SPDX-License-Identifier: Apache-2.0
"""Cheap checks run on an uploaded file before any parser touches it."""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from typing import Literal

FileKind = Literal["pdf", "docx"]

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIME_FOR: dict[FileKind, str] = {"pdf": PDF_MIME, "docx": DOCX_MIME}

MAX_ZIP_MEMBERS = 200
MAX_ZIP_UNCOMPRESSED = 20 * 1024 * 1024
MAX_ZIP_RATIO = 100
_RATIO_FLOOR = 64 * 1024  # small members can have extreme ratios harmlessly


class RejectedFile(Exception):
    """The file is not something we will parse. ``code`` is safe to show the user."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def sniff(data: bytes) -> FileKind | None:
    """Identify the file by its leading bytes, never by name or declared type."""
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        return "docx"
    return None


def check_type(data: bytes, declared_mime: str) -> FileKind:
    kind = sniff(data)
    declared = declared_mime.split(";", 1)[0].strip().lower()
    if kind is None or MIME_FOR[kind] != declared:
        raise RejectedFile(415, "unsupported_file", "Upload a PDF or Word (.docx) file.")
    if kind == "docx":
        check_docx_archive(data)
    return kind


def check_docx_archive(data: bytes) -> None:
    """Reject zip bombs and odd archives using only the central directory."""
    bad = RejectedFile(422, "unreadable_file", "We couldn't read this file.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            members = zf.infolist()
    except (zipfile.BadZipFile, ValueError, OSError):
        raise bad from None
    if len(members) > MAX_ZIP_MEMBERS:
        raise bad
    total = 0
    names = set()
    for m in members:
        name = m.filename
        if name.startswith(("/", "\\")) or ".." in name.replace("\\", "/").split("/"):
            raise bad
        if m.file_size < 0 or m.compress_size < 0:
            raise bad
        total += m.file_size
        if total > MAX_ZIP_UNCOMPRESSED:
            raise bad
        if m.file_size > _RATIO_FLOOR and m.file_size > MAX_ZIP_RATIO * max(m.compress_size, 1):
            raise bad
        names.add(name)
    if "word/document.xml" not in names:
        raise RejectedFile(415, "unsupported_file", "Upload a PDF or Word (.docx) file.")


_UNSAFE_NAME = re.compile(r"[^\w .()\-]+")


def clean_filename(raw: str | None, kind: str) -> str:
    """A display-only name: no paths, no control characters, bounded length."""
    name = unicodedata.normalize("NFKC", raw or "")
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = _UNSAFE_NAME.sub("_", name).strip(" ._")[:120]
    if not name:
        name = "resume"
    if not name.lower().endswith(f".{kind}"):
        name = f"{name}.{kind}"
    return name
