# SPDX-License-Identifier: Apache-2.0
"""api access: the 'full' scope

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-06 12:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _scopes(names: str) -> None:
    op.drop_constraint(op.f("ck_api_tokens_scopes_known"), "api_tokens", type_="check")
    op.create_check_constraint(
        op.f("ck_api_tokens_scopes_known"), "api_tokens", f"scopes <@ ARRAY[{names}]::varchar[]"
    )


def upgrade() -> None:
    _scopes("'read', 'draft', 'propose', 'full'")


def downgrade() -> None:
    # Keys with full access can't be expressed in the old scheme: remove them.
    op.execute("DELETE FROM api_tokens WHERE 'full' = ANY(scopes)")
    _scopes("'read', 'draft', 'propose'")
