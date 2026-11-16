# SPDX-License-Identifier: Apache-2.0
"""discovery: more job source kinds (company boards, remote boards, HN, job alert emails)

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-15 12:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = ("greenhouse", "lever", "ashby", "adzuna", "usajobs", "jsearch", "rss")
NEW = (
    "greenhouse",
    "lever",
    "ashby",
    "workable",
    "smartrecruiters",
    "recruitee",
    "personio",
    "adzuna",
    "usajobs",
    "jsearch",
    "rss",
    "remotive",
    "remoteok",
    "himalayas",
    "jobicy",
    "arbeitnow",
    "hn_hiring",
    "email_alerts",
)


def _kinds(values: tuple[str, ...]) -> None:
    op.drop_constraint(op.f("ck_job_sources_kind"), "job_sources", type_="check")
    allowed = ", ".join(f"'{v}'" for v in values)
    op.create_check_constraint(op.f("ck_job_sources_kind"), "job_sources", f"kind IN ({allowed})")


def upgrade() -> None:
    _kinds(NEW)


def downgrade() -> None:
    allowed = ", ".join(f"'{v}'" for v in OLD)
    op.execute(f"DELETE FROM job_sources WHERE kind NOT IN ({allowed})")  # noqa: S608
    _kinds(OLD)
