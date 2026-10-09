"""market_results: one record per finished market. [3.4] #12

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-09

Written from the model and reviewed line by line; unit_test/test_migrations.py
proves it builds what the model builds, the CHECK included. `kind` is a
non-native Enum: a varchar with no CHECK, so its value list is documentation.
The paired CHECK is what constrains it. No ON DELETE on either key, and no
append-only trigger: D-NEW "`ledger.market_results` carries no append-only
trigger".
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_results",
        sa.Column("market_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "settled",
                name="ledger_result_kind",
                native_enum=False,
                length=24,
            ),
            nullable=False,
        ),
        sa.Column("outcome_id", sa.Uuid(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(kind = 'settled') = (outcome_id IS NOT NULL)",
            name="ck_market_results_outcome_iff_settled",
        ),
        sa.ForeignKeyConstraint(
            ["market_id", "outcome_id"],
            ["ledger.market_outcomes.market_id", "ledger.market_outcomes.outcome_id"],
            name="fk_market_results_market_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["market_id"],
            ["ledger.market_books.market_id"],
            name="fk_market_results_book",
        ),
        sa.PrimaryKeyConstraint("market_id"),
        schema="ledger",
    )


def downgrade() -> None:
    op.drop_table("market_results", schema="ledger")
