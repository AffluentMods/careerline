# SPDX-License-Identifier: Apache-2.0
"""api access: API keys, API-sourced conversations and vault entries

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-05 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.String(length=16)), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("cardinality(scopes) > 0", name=op.f("ck_api_tokens_scopes_not_empty")),
        sa.CheckConstraint(
            "scopes <@ ARRAY['read', 'draft', 'propose']::varchar[]",
            name=op.f("ck_api_tokens_scopes_known"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_api_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_api_tokens_token_hash")),
    )
    op.create_index(op.f("ix_api_tokens_user_id"), "api_tokens", ["user_id"], unique=False)

    op.add_column(
        "conversations",
        sa.Column("source", sa.String(length=8), server_default="chat", nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_conversations_source"), "conversations", "source IN ('chat', 'api')"
    )

    op.drop_constraint(op.f("ck_vault_entries_source"), "vault_entries", type_="check")
    op.create_check_constraint(
        op.f("ck_vault_entries_source"), "vault_entries", "source IN ('manual', 'import', 'api')"
    )


def downgrade() -> None:
    op.execute("UPDATE vault_entries SET source = 'manual' WHERE source = 'api'")
    op.drop_constraint(op.f("ck_vault_entries_source"), "vault_entries", type_="check")
    op.create_check_constraint(
        op.f("ck_vault_entries_source"), "vault_entries", "source IN ('manual', 'import')"
    )
    op.drop_constraint(op.f("ck_conversations_source"), "conversations", type_="check")
    op.drop_column("conversations", "source")
    op.drop_index(op.f("ix_api_tokens_user_id"), table_name="api_tokens")
    op.drop_table("api_tokens")
