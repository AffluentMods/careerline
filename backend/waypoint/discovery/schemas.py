# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints

from waypoint.vault.schemas import Short, Strict, Url

SourceKind = Literal["greenhouse", "lever", "ashby", "adzuna", "usajobs", "jsearch", "rss"]
Shift = Literal["day", "early", "evening", "night", "weekend", "rotating", "on_call"]
Clearance = Literal["none", "public_trust", "secret", "top_secret"]
SourceName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Phrase = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
CompanyName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Interval = Annotated[int, Field(ge=1, le=168)]


class SourceIn(Strict):
    kind: SourceKind
    name: SourceName
    enabled: bool = True
    interval_hours: Interval = 12
    config: dict[str, Any] = Field(default_factory=dict)
    secret: dict[str, Any] | None = None


class SourcePatch(Strict):
    name: SourceName | None = None
    enabled: bool | None = None
    interval_hours: Interval | None = None
    config: dict[str, Any] | None = None
    # Leave out to keep the stored secret; send a full object to replace it.
    secret: dict[str, Any] | None = None


class SourceOut(BaseModel):
    id: uuid.UUID
    kind: str
    label: str
    name: str
    enabled: bool
    interval_hours: int
    config: dict[str, Any]
    has_secret: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    last_status: str
    last_error: str
    last_found: int
    last_new: int
    pending: bool
    created_at: datetime


class AdapterOut(BaseModel):
    kind: str
    label: str
    description: str
    terms_url: str
    config_fields: list[dict[str, Any]]
    secret_fields: list[dict[str, Any]]


class FilterOut(BaseModel):
    passed: bool
    reasons: list[str]
    preferred: bool
    pay_vs_target: str | None
    distance_miles: float | None


class ScoreOut(BaseModel):
    score: int
    method: str
    summary: str
    stale: bool


class PostingSummary(BaseModel):
    id: uuid.UUID
    title: str
    company: str
    location: str
    work_mode: str | None
    pay_min_cents: int | None
    pay_max_cents: int | None
    pay_period: str | None
    currency: str
    shifts: list[str]
    clearance: str | None
    employment_type: str
    url: str
    posted_at: datetime | None
    closes_on: date | None
    first_seen_at: datetime
    last_seen_at: datetime
    status: str
    application_id: uuid.UUID | None
    sources: list[str]
    filter: FilterOut
    score: ScoreOut | None = None


class SightingOut(BaseModel):
    source_label: str
    url: str
    seen_at: datetime


class PostingDetail(PostingSummary):
    description: str
    sightings: list[SightingOut]
    score_detail: dict[str, Any] | None = None


class PostingList(BaseModel):
    items: list[PostingSummary]
    counts: dict[str, int]


class PostingPatch(Strict):
    status: Literal["new", "saved", "dismissed"]


class ImportIn(Strict):
    url: Url = ""
    text: Annotated[str, StringConstraints(max_length=50_000)] = ""
    title: Short = ""
    company: Short = ""
    location: Short = ""


class FiltersIn(Strict):
    locations: list[Phrase] = Field(default_factory=list, max_length=30)
    home_lat: Annotated[float, Field(ge=-90, le=90)] | None = None
    home_lon: Annotated[float, Field(ge=-180, le=180)] | None = None
    radius_miles: Annotated[int, Field(ge=1, le=500)] | None = None
    remote_ok: bool = True
    hybrid_ok: bool = True
    onsite_ok: bool = True
    min_hourly_cents: Annotated[int, Field(ge=0, le=1_000_000)] | None = None
    target_hourly_cents: Annotated[int, Field(ge=0, le=1_000_000)] | None = None
    hide_unpaid_postings: bool = False
    shifts_wanted: list[Shift] = Field(default_factory=list)
    shifts_excluded: list[Shift] = Field(default_factory=list)
    exclude_keywords: list[Phrase] = Field(default_factory=list, max_length=50)
    require_keywords: list[Phrase] = Field(default_factory=list, max_length=50)
    clearance_held: Clearance = "none"
    preferred_companies: list[CompanyName] = Field(default_factory=list, max_length=100)
    blocked_companies: list[CompanyName] = Field(default_factory=list, max_length=100)


class FiltersOut(FiltersIn):
    model_config = {"extra": "ignore"}  # noqa: RUF012
