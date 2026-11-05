# SPDX-License-Identifier: Apache-2.0
"""Validation for vault data.

Each entry kind has its own strict schema for the JSONB ``data`` column. The API
accepts ``{"kind": ..., "data": {...}}`` and validates ``data`` against the
schema for that kind with :func:`parse_data`.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from fastapi.exceptions import RequestValidationError
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
)

Kind = Literal[
    "role",
    "responsibility",
    "achievement",
    "project",
    "certification",
    "education",
    "skill",
    "link",
]

_PARTIAL_DATE = re.compile(r"(19|20)\d{2}(-(0[1-9]|1[0-2]))?")


def _partial_date(value: str) -> str:
    if not _PARTIAL_DATE.fullmatch(value):
        raise ValueError("use YYYY or YYYY-MM")
    return value


def _web_url(value: str) -> str:
    if not value:
        return value
    parts = urlsplit(value)
    local = parts.hostname in {"localhost", "127.0.0.1", "::1"}
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise ValueError("must be an https:// link")
    if not parts.hostname or parts.username or parts.password:
        raise ValueError("must be a plain web link")
    return value


Tiny = Annotated[str, StringConstraints(strip_whitespace=True, max_length=50)]
TinyReq = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
Brief = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]
BriefReq = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Short = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Address = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]
Bullet = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
Long = Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)]
PartialDate = Annotated[str, AfterValidator(_partial_date)]
Url = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=500), AfterValidator(_web_url)
]
UrlReq = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
    AfterValidator(_web_url),
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- per-kind data ---------------------------------------------------------------------


class RoleData(Strict):
    title: Name
    employer: Short = ""
    location: Short = ""
    employment_type: (
        Literal["full_time", "part_time", "contract", "internship", "temporary", "volunteer"] | None
    ) = None
    start: PartialDate | None = None
    end: PartialDate | None = None  # None means current
    summary: Long = ""


class ResponsibilityData(Strict):
    text: Bullet


class Metric(Strict):
    value: TinyReq
    unit: Tiny = ""
    context: Short = ""


class AchievementData(Strict):
    text: Bullet
    metric: Metric | None = None


class ProjectData(Strict):
    name: Name
    your_role: Short = ""
    start: PartialDate | None = None
    end: PartialDate | None = None
    description: Long = ""
    url: Url = ""


class CertificationData(Strict):
    name: Name
    issuer: Short = ""
    status: Literal["earned", "in_progress", "expired"] = "earned"
    earned_on: PartialDate | None = None
    expires_on: PartialDate | None = None
    target_date: PartialDate | None = None
    credential_id: Brief = ""
    url: Url = ""


class EducationData(Strict):
    institution: Name
    credential: Short = ""
    field: Short = ""
    status: Literal["completed", "in_progress"] = "completed"
    start: PartialDate | None = None
    end: PartialDate | None = None


class SkillData(Strict):
    name: BriefReq
    category: Brief = ""
    level: Literal["familiar", "proficient", "expert"] | None = None


class LinkData(Strict):
    label: BriefReq
    url: UrlReq


DATA_MODELS: dict[str, type[Strict]] = {
    "role": RoleData,
    "responsibility": ResponsibilityData,
    "achievement": AchievementData,
    "project": ProjectData,
    "certification": CertificationData,
    "education": EducationData,
    "skill": SkillData,
    "link": LinkData,
}

# Which kinds may be the parent of each kind. ``None`` means "no parent" is allowed.
ALLOWED_PARENTS: dict[str, frozenset[str | None]] = {
    "role": frozenset({None}),
    "responsibility": frozenset({"role"}),
    "achievement": frozenset({"role", "project", "education", None}),
    "project": frozenset({"role", None}),
    "certification": frozenset({None}),
    "education": frozenset({None}),
    "skill": frozenset({None}),
    "link": frozenset({None}),
}


def parse_data(kind: str, raw: Any) -> dict[str, Any]:
    """Validate ``raw`` for ``kind`` and return the normalised dict.

    Raises the same 422 error as any other body validation failure.
    """
    model = DATA_MODELS[kind]
    try:
        parsed = model.model_validate(raw)
    except ValidationError as exc:
        raise RequestValidationError(
            [
                {"loc": ("body", "data", *e["loc"]), "msg": e["msg"], "type": e["type"]}
                for e in exc.errors()
            ]
        ) from None
    return parsed.model_dump(mode="json")


def sort_date(kind: str, data: dict[str, Any]) -> str | None:
    """Newest-first ordering key: the end date, a far-future value if current, else start."""
    if kind == "certification":
        result = data.get("earned_on") or data.get("target_date")
        return str(result) if result else None
    start, end = data.get("start"), data.get("end")
    if end:
        return str(end)
    if start:
        return "9999" if kind in {"role", "project", "education"} else str(start)
    return None


# --- request / response bodies -------------------------------------------------------


class EntryIn(Strict):
    kind: Kind
    parent_id: uuid.UUID | None = None
    data: dict[str, Any]
    evidence_note: Note = ""
    verified: bool = True


class EntryPatch(Strict):
    kind: Kind | None = None
    parent_id: uuid.UUID | None = None
    data: dict[str, Any] | None = None
    evidence_note: Note | None = None
    verified: bool | None = None


class EntryOut(BaseModel):
    id: uuid.UUID
    kind: str
    parent_id: uuid.UUID | None
    data: dict[str, Any]
    evidence_note: str
    verified: bool
    verified_at: datetime | None
    # True only when this entry and every ancestor are verified: the AI may use it.
    usable: bool
    source: str
    import_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class VerifyIn(Strict):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    verified: bool


class CountOut(BaseModel):
    updated: int


class KindCount(BaseModel):
    total: int
    verified: int


class SummaryOut(BaseModel):
    kinds: dict[str, KindCount]
    stories: KindCount
    needs_review: int


_EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,63}")


def _email_or_blank(value: str) -> str:
    if value and not _EMAIL.fullmatch(value):
        raise ValueError("not a valid email address")
    return value


class ProfileIn(Strict):
    full_name: Short = ""
    headline: Short = ""
    location_text: Short = ""
    summary: Long = ""
    email: Annotated[
        str,
        StringConstraints(strip_whitespace=True, max_length=320),
        AfterValidator(_email_or_blank),
    ] = ""
    phone: Tiny = ""
    address: Address = ""


class ProfileOut(ProfileIn):
    model_config = ConfigDict(extra="ignore")


Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]


class StoryIn(Strict):
    title: Name
    situation: Long = ""
    task: Long = ""
    action: Long = ""
    result: Long = ""
    reflection: Long = ""
    tags: list[Tag] = Field(default_factory=list, max_length=20)
    entry_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    verified: bool = True


class StoryPatch(Strict):
    title: Name | None = None
    situation: Long | None = None
    task: Long | None = None
    action: Long | None = None
    result: Long | None = None
    reflection: Long | None = None
    tags: list[Tag] | None = Field(default=None, max_length=20)
    entry_ids: list[uuid.UUID] | None = Field(default=None, max_length=50)
    verified: bool | None = None


class StoryOut(BaseModel):
    id: uuid.UUID
    title: str
    situation: str
    task: str
    action: str
    result: str
    reflection: str
    tags: list[str]
    entry_ids: list[uuid.UUID]
    verified: bool
    verified_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ImportOut(BaseModel):
    id: uuid.UUID
    filename: str
    mime: str
    size_bytes: int
    stats: dict[str, Any]
    drafts_remaining: int
    created_at: datetime


class ProfileSuggestions(BaseModel):
    full_name: str = ""
    headline: str = ""
    location_text: str = ""
    summary: str = ""
    email: str = ""
    phone: str = ""


class ImportDetailOut(ImportOut):
    text: str
    suggestions: ProfileSuggestions
    # Every entry still linked to this import, verified or not.
    entries: list[EntryOut]
