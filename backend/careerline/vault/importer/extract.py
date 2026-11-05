# SPDX-License-Identifier: Apache-2.0
"""Run the extraction worker in a separate process with a timeout.

The child gets a minimal environment, so it never sees the database URL or
encryption keys, and it is killed if it runs past the timeout.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys

import anyio

from careerline.vault.importer.guards import FileKind, RejectedFile

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 15.0
_PASS_ENV = ("SYSTEMROOT", "TEMP", "TMP", "LANG", "LC_ALL")


def _child_env() -> dict[str, str]:
    return {k: v for k in _PASS_ENV if (v := os.environ.get(k))}


def extract_text_sync(data: bytes, kind: FileKind, timeout: float = TIMEOUT_SECONDS) -> str:
    unreadable = RejectedFile(422, "unreadable_file", "We couldn't read this file.")
    try:
        proc = subprocess.run(  # noqa: S603  (fixed argv, no shell)
            [sys.executable, "-I", "-m", "careerline.vault.importer.worker", kind],
            input=data,
            capture_output=True,
            timeout=timeout,
            env=_child_env(),
            check=False,
        )
    except subprocess.TimeoutExpired:
        log.warning("resume extraction timed out (%s, %d bytes)", kind, len(data))
        raise RejectedFile(
            422, "unreadable_file", "This file took too long to read. Try saving it again."
        ) from None
    if proc.returncode != 0:
        log.warning(
            "resume extraction failed (%s, exit %d): %s",
            kind,
            proc.returncode,
            proc.stderr[:300].decode("utf-8", "replace").strip(),
        )
        raise unreadable
    try:
        text = json.loads(proc.stdout)["text"]
    except (ValueError, KeyError, TypeError):
        raise unreadable from None
    if not isinstance(text, str):
        raise unreadable
    return text


async def extract_text(data: bytes, kind: FileKind) -> str:
    return await anyio.to_thread.run_sync(extract_text_sync, data, kind, TIMEOUT_SECONDS)
