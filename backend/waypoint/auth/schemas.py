# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]+$")


def normalise_email(value: str) -> str:
    value = value.strip().lower()
    if not _EMAIL_RE.match(value):
        raise ValueError("Enter a valid email address.")
    return value


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_max_length=1000)


class RegisterIn(_In):
    email: str = Field(max_length=320)
    password: str = Field(max_length=256)
    display_name: str = Field(min_length=1, max_length=100)

    _email = field_validator("email")(normalise_email)

    @field_validator("display_name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Enter your name.")
        return v


class LoginIn(_In):
    email: str = Field(max_length=320)
    password: str = Field(max_length=256)

    _email = field_validator("email")(normalise_email)


class CodeIn(_In):
    code: str = Field(min_length=6, max_length=32)


class PasswordIn(_In):
    password: str = Field(max_length=256)


class PasswordAndCodeIn(_In):
    password: str = Field(max_length=256)
    code: str = Field(min_length=6, max_length=32)


class ChangePasswordIn(_In):
    # Empty for accounts that have no password yet (single sign-on only).
    current_password: str = Field(default="", max_length=256)
    new_password: str = Field(max_length=256)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    is_owner: bool
    mfa_enabled: bool
    has_password: bool = True


class SsoStatus(BaseModel):
    enabled: bool
    label: str


class StatusOut(BaseModel):
    has_users: bool
    registration_open: bool
    session: Literal["none", "mfa_pending", "active"]
    password_login: bool = True
    sso: SsoStatus = SsoStatus(enabled=False, label="")
    demo: bool = False


class ReauthIn(_In):
    password: str = Field(max_length=256)


class ReauthOut(BaseModel):
    until: datetime


class LoginOut(BaseModel):
    status: Literal["ok", "mfa_required"]
    user: UserOut | None = None


class MfaSetupOut(BaseModel):
    secret: str
    otpauth_uri: str


class RecoveryCodesOut(BaseModel):
    recovery_codes: list[str]


class SessionOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime
    ip: str | None
    user_agent: str | None
    current: bool
