"""Baseline: the ledger schema as create_all built it before #75. [F-5] #75

Revision ID: 0001
Revises:
Create Date: 2026-10-04

Written from the models and reviewed line by line; unit_test/test_migrations.py
proves it builds exactly what they build, the CHECKs and the trigger included.
Folds in sql/migrations/0007 (idempotency_key at 255). The `kind` columns are
non-native Enums: varchars with no CHECK, so their value lists are
documentation, not constraints.

The append-only trigger is installed by the same two DDL elements the model's
`after_create` events run, imported rather than copied, so there is one copy of
that SQL. Run as DDL elements and never as `.statement` strings: a string goes
through `text()`, which would read a `:name` in the body as a bind. ADR 0020.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from model.entities import INSTALL_APPEND_ONLY_TRIGGER, REJECT_MUTATION_FUNCTION

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "user",
                "platform",
                "market_pool",
                name="ledger_account_kind",
                native_enum=False,
                length=24,
            ),
            nullable=False,
        ),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "owner_id", name="uq_accounts_kind_owner"),
        schema="ledger",
    )
    op.create_table(
        "transactions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "signup_grant",
                "market_seed",
                "trade_buy",
                "trade_sell",
                name="ledger_transaction_kind",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        # 255, not 120: sql/migrations/0007, [T-2] #22.
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
        schema="ledger",
    )
    op.create_table(
        "entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("transaction_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount <> 0", name="ck_entries_amount_nonzero"),
        sa.ForeignKeyConstraint(["account_id"], ["ledger.accounts.id"]),
        sa.ForeignKeyConstraint(["transaction_id"], ["ledger.transactions.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema="ledger",
    )
    op.create_index(
        "ix_ledger_entries_account_feed",
        "entries",
        ["account_id", sa.text("created_at DESC"), sa.text("id DESC")],
        unique=False,
        schema="ledger",
    )
    op.create_index(
        op.f("ix_ledger_entries_transaction_id"),
        "entries",
        ["transaction_id"],
        unique=False,
        schema="ledger",
    )
    # ADR 0009: entries are append-only, at the database.
    op.execute(REJECT_MUTATION_FUNCTION)
    op.execute(INSTALL_APPEND_ONLY_TRIGGER)
    op.create_table(
        "market_books",
        sa.Column("market_id", sa.Uuid(), nullable=False),
        sa.Column("liquidity_b", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("seed_subsidy", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("pool_account_id", sa.Uuid(), nullable=False),
        sa.Column("state_version", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("state_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("state_version >= 0", name="ck_market_books_state_version_nonneg"),
        sa.ForeignKeyConstraint(["pool_account_id"], ["ledger.accounts.id"]),
        sa.PrimaryKeyConstraint("market_id"),
        schema="ledger",
    )
    op.create_table(
        "market_outcomes",
        sa.Column("market_id", sa.Uuid(), nullable=False),
        sa.Column("outcome_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "q", sa.Numeric(precision=18, scale=4), server_default=sa.text("0"), nullable=False
        ),
        sa.ForeignKeyConstraint(["market_id"], ["ledger.market_books.market_id"]),
        sa.PrimaryKeyConstraint("market_id", "outcome_id"),
        sa.UniqueConstraint("market_id", "position", name="uq_market_outcomes_market_position"),
        schema="ledger",
    )
    op.create_table(
        "positions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("market_id", sa.Uuid(), nullable=False),
        sa.Column("outcome_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("cost_basis", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("cost_basis >= 0", name="ck_positions_cost_basis_nonneg"),
        sa.CheckConstraint("quantity >= 0", name="ck_positions_quantity_nonneg"),
        sa.ForeignKeyConstraint(
            ["market_id", "outcome_id"],
            ["ledger.market_outcomes.market_id", "ledger.market_outcomes.outcome_id"],
            name="fk_positions_market_outcome",
        ),
        sa.PrimaryKeyConstraint("user_id", "market_id", "outcome_id"),
        schema="ledger",
    )


def downgrade() -> None:
    op.drop_table("positions", schema="ledger")
    op.drop_table("market_outcomes", schema="ledger")
    op.drop_table("market_books", schema="ledger")
    # The trigger before its function, which cannot be dropped while in use.
    # The function is not a table, so dropping the tables would leave it behind.
    op.execute("DROP TRIGGER entries_append_only ON ledger.entries")
    op.execute("DROP FUNCTION ledger.reject_mutation()")
    op.drop_index(op.f("ix_ledger_entries_transaction_id"), table_name="entries", schema="ledger")
    op.drop_index("ix_ledger_entries_account_feed", table_name="entries", schema="ledger")
    op.drop_table("entries", schema="ledger")
    op.drop_table("transactions", schema="ledger")
    op.drop_table("accounts", schema="ledger")
