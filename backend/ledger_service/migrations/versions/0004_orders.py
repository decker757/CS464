"""orders: limit orders resting against the AMM. [T-6] #26

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-18

Written from the model and reviewed line by line; unit_test/test_migrations.py
proves it builds what the model builds, the CHECKs included, and holds the two
partial indexes' predicates by name, which the metadata comparison cannot see.
`seq` is GENERATED ALWAYS AS IDENTITY: the database numbers an order, so the
sequence is the order the inserts took the book lock in. ADR 0024, and
DECISIONS.md, "Priority is price, then an identity `seq` assigned under the book
lock".
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("market_id", sa.Uuid(), nullable=False),
        sa.Column("outcome_id", sa.Uuid(), nullable=False),
        sa.Column(
            "side",
            sa.Enum("buy", "sell", name="ledger_order_side", native_enum=False, length=24),
            nullable=False,
        ),
        sa.Column("limit_price", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column(
            "filled_quantity",
            sa.Numeric(precision=18, scale=4),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("budget", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column(
            "spent",
            sa.Numeric(precision=18, scale=4),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "open",
                "filled",
                "cancelled",
                name="ledger_order_status",
                native_enum=False,
                length=24,
            ),
            nullable=False,
        ),
        sa.Column("book_state_version", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "limit_price > 0 AND limit_price < 1",
            name="ck_orders_limit_price_between_0_and_1",
        ),
        sa.CheckConstraint("quantity > 0", name="ck_orders_quantity_positive"),
        sa.CheckConstraint("filled_quantity >= 0", name="ck_orders_filled_quantity_nonneg"),
        sa.CheckConstraint(
            "filled_quantity <= quantity", name="ck_orders_filled_quantity_within_quantity"
        ),
        sa.CheckConstraint(
            "(side = 'buy') = (budget IS NOT NULL)", name="ck_orders_budget_iff_buy"
        ),
        sa.CheckConstraint("spent >= 0", name="ck_orders_spent_nonneg"),
        sa.CheckConstraint(
            "budget IS NULL OR spent <= budget", name="ck_orders_spent_within_budget"
        ),
        sa.CheckConstraint(
            "(status = 'filled') = (filled_quantity = quantity)",
            name="ck_orders_filled_iff_nothing_remains",
        ),
        sa.CheckConstraint(
            "(status = 'cancelled') = (cancelled_at IS NOT NULL)",
            name="ck_orders_cancelled_at_iff_cancelled",
        ),
        sa.ForeignKeyConstraint(
            ["market_id", "outcome_id"],
            ["ledger.market_outcomes.market_id", "ledger.market_outcomes.outcome_id"],
            name="fk_orders_market_outcome",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_orders_idempotency_key"),
        schema="ledger",
    )
    op.create_index(
        "ix_ledger_orders_open_by_market",
        "orders",
        ["market_id", "seq"],
        unique=False,
        schema="ledger",
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_index(
        "ix_ledger_orders_open_by_user",
        "orders",
        ["user_id"],
        unique=False,
        schema="ledger",
        postgresql_where=sa.text("status = 'open'"),
    )


def downgrade() -> None:
    op.drop_index("ix_ledger_orders_open_by_user", table_name="orders", schema="ledger")
    op.drop_index("ix_ledger_orders_open_by_market", table_name="orders", schema="ledger")
    op.drop_table("orders", schema="ledger")
