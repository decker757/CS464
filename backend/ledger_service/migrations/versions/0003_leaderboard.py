"""leaderboard_generations and leaderboard_entries: the ranking, stored whole. [L-1] #38

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-11

Written from the model and reviewed line by line; unit_test/test_migrations.py
proves it builds what the model builds, the CHECK included. The foreign key is
the one ON DELETE in this schema: the refresh prunes generations and the
database takes their entries with them. DECISIONS.md, "The leaderboard is
stored as generations, and a refresh swaps the whole ranking in one commit".
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "leaderboard_generations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ranked_users", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema="ledger",
    )
    op.create_table(
        "leaderboard_entries",
        sa.Column("generation_id", sa.Uuid(), nullable=False),
        sa.Column("rank", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(length=32), nullable=False),
        sa.Column("net_worth", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.CheckConstraint("rank > 0", name="ck_leaderboard_entries_rank_positive"),
        sa.ForeignKeyConstraint(
            ["generation_id"],
            ["ledger.leaderboard_generations.id"],
            name="fk_leaderboard_entries_generation",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("generation_id", "rank"),
        sa.UniqueConstraint(
            "generation_id", "user_id", name="uq_leaderboard_entries_generation_user"
        ),
        schema="ledger",
    )


def downgrade() -> None:
    op.drop_table("leaderboard_entries", schema="ledger")
    op.drop_table("leaderboard_generations", schema="ledger")
