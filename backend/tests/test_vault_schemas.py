# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from typing import get_args

import pytest
from fastapi.exceptions import RequestValidationError

from waypoint.models import ENTRY_KINDS
from waypoint.vault.schemas import (
    ALLOWED_PARENTS,
    DATA_MODELS,
    Kind,
    ProfileIn,
    parse_data,
    sort_date,
)


def test_kind_lists_agree() -> None:
    assert set(get_args(Kind)) == set(ENTRY_KINDS) == set(DATA_MODELS) == set(ALLOWED_PARENTS)


@pytest.mark.parametrize(
    ("kind", "data"),
    [
        ("role", {"title": "Security Analyst", "employer": "Northwind Tools", "start": "2021-03"}),
        ("responsibility", {"text": "Triaged alerts from the SIEM."}),
        (
            "achievement",
            {
                "text": "Cut phishing clicks by 40%",
                "metric": {"value": "40", "unit": "%", "context": ""},
            },
        ),
        ("project", {"name": "Home lab", "url": "https://example.com/lab"}),
        ("certification", {"name": "Example Cert", "status": "in_progress", "target_date": "2027"}),
        ("education", {"institution": "Example State University", "status": "completed"}),
        ("skill", {"name": "Python", "category": "Languages", "level": "proficient"}),
        ("link", {"label": "Portfolio", "url": "https://example.com"}),
    ],
)
def test_valid_data_per_kind(kind: str, data: dict[str, object]) -> None:
    out = parse_data(kind, data)
    for key, value in data.items():
        assert out[key] == value


@pytest.mark.parametrize(
    ("kind", "data"),
    [
        ("role", {"employer": "No title"}),
        ("role", {"title": "x", "start": "2021-13"}),
        ("role", {"title": "x", "start": "March 2021"}),
        ("role", {"title": "x", "unknown": 1}),
        ("role", {"title": "   "}),
        ("achievement", {"text": "x" * 1001}),
        ("certification", {"name": "x", "status": "maybe"}),
        ("link", {"label": "x", "url": "javascript:alert(1)"}),
        ("link", {"label": "x", "url": "http://example.com"}),
        ("link", {"label": "x", "url": "https://user:pw@example.com"}),
        ("link", {"label": "x"}),
        ("skill", {"name": "x", "level": "guru"}),
    ],
)
def test_invalid_data_rejected(kind: str, data: dict[str, object]) -> None:
    with pytest.raises(RequestValidationError) as exc:
        parse_data(kind, data)
    assert all(e["loc"][:2] == ("body", "data") for e in exc.value.errors())


def test_whitespace_is_trimmed_and_local_http_allowed() -> None:
    assert parse_data("skill", {"name": "  Go  "})["name"] == "Go"
    assert parse_data("link", {"label": "Dev", "url": "http://localhost:3000"})["url"]


def test_sort_date() -> None:
    assert sort_date("role", {"start": "2020", "end": "2022-05"}) == "2022-05"
    assert sort_date("role", {"start": "2020", "end": None}) == "9999"
    assert sort_date("certification", {"earned_on": "2023-01"}) == "2023-01"
    assert sort_date("skill", {"name": "x"}) is None


def test_profile_email_validation() -> None:
    assert ProfileIn(email="").email == ""
    with pytest.raises(ValueError, match="email"):
        ProfileIn(email="not-an-email")
