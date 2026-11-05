# SPDX-License-Identifier: Apache-2.0
"""Encryption contexts: the associated data that binds each encrypted value to its place.

A ciphertext only decrypts with the exact context it was written with, so a value copied
into another row or column is useless. Every module that encrypts or decrypts a field takes
its context from here, and so does key re-encryption (``waypoint.data.rekey``), which must
reproduce each one exactly.
"""

from __future__ import annotations

import uuid

OIDC_FLOW = "oidc:flow"


def mfa(user_id: uuid.UUID, pending: bool = False) -> str:
    return f"user:{user_id}:{'mfa_pending' if pending else 'mfa_secret'}"


def ai_key(user_id: uuid.UUID) -> str:
    return f"user:{user_id}:ai_api_key"


def source_secret(user_id: uuid.UUID, source_id: uuid.UUID) -> str:
    return f"user:{user_id}:source:{source_id}:secret"


def profile(user_id: uuid.UUID, field: str) -> str:
    return f"user:{user_id}:profile:{field}"


def resume_import(user_id: uuid.UUID, import_id: uuid.UUID, part: str) -> str:
    return f"user:{user_id}:import:{import_id}:{part}"


def contact(user_id: uuid.UUID, contact_id: uuid.UUID, field: str) -> str:
    return f"user:{user_id}:contact:{contact_id}:{field}"


def attachment(user_id: uuid.UUID, attachment_id: uuid.UUID) -> str:
    return f"user:{user_id}:attachment:{attachment_id}"


def notify(user_id: uuid.UUID, field: str) -> str:
    return f"user:{user_id}:notify:{field}"
