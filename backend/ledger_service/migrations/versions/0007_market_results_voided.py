"""market_results: a voided market's reason and administrator. [BE] #221

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-25

ADR 0025. `reason` and `voided_by` are nullable, and each is set exactly when
`kind = 'voided'`, by a CHECK written here by hand: autogenerate cannot see a
CHECK, and unit_test/test_migrations.py compares the catalog to hold the two
together. `ResultKind.VOIDED`, `TransactionKind.VOID_REFUND` and
`TransactionKind.VOID_RESIDUE` need no DDL: both columns are non-native enums,
plain varchars with no CHECK on their values. A void names no winner, which
`ck_market_results_outcome_iff_settled` already admits. Every existing row is
a settlement, so both new CHECKs hold on it with both columns null.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "market_results",
        sa.Column("reason", sa.Text(), nullable=True),
        schema="ledger",
    )
    op.add_column(
        "market_results",
        sa.Column("voided_by", sa.Uuid(), nullable=True),
        schema="ledger",
    )
    op.create_check_constraint(
        "ck_market_results_reason_iff_voided",
        "market_results",
        "(kind = 'voided') = (reason IS NOT NULL)",
        schema="ledger",
    )
    op.create_check_constraint(
        "ck_market_results_voided_by_iff_voided",
        "market_results",
        "(kind = 'voided') = (voided_by IS NOT NULL)",
        schema="ledger",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_market_results_voided_by_iff_voided",
        "market_results",
        schema="ledger",
        type_="check",
    )
    op.drop_constraint(
        "ck_market_results_reason_iff_voided",
        "market_results",
        schema="ledger",
        type_="check",
    )
    op.drop_column("market_results", "voided_by", schema="ledger")
    op.drop_column("market_results", "reason", schema="ledger")
