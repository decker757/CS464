"""orders: filled_quantity and filled_value move together. [T-6] #26, #397

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-19

Hand-written, because autogenerate cannot see a CHECK constraint (ADR 0020) and
so will never offer this one. The upgrade counts the rows that would violate it
and says what it found, rather than leaving Postgres to reject the ALTER on a
database nobody has looked at.

A backstop rather than a fix: `core/pricing.py::quantize_cost` already refuses a
quantized zero on both sides, so this rejects no row the code can produce. #397
has the reasoning and the damage it guards against.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger(__name__)

CONSTRAINT = "ck_orders_filled_quantity_iff_filled_value"
CONDITION = "(filled_quantity > 0) = (filled_value > 0)"


def upgrade() -> None:
    violating = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM ledger.orders "
                "WHERE (filled_quantity > 0) <> (filled_value > 0)"
            )
        )
        .scalar_one()
    )
    if violating:
        raise RuntimeError(
            f"{violating} row(s) of ledger.orders have one of filled_quantity and "
            "filled_value advanced without the other, so Postgres will refuse the "
            "CHECK. A buy like that reserves credits its fills have already spent. "
            "The fills are in ledger.transactions, whose context names the "
            "order_id, with their legs in ledger.entries by transaction_id: "
            "reconcile the rollups against those, then run the step again. This "
            "migration has changed nothing."
        )
    logger.info("ledger.orders: no row violates %s (%s); adding it.", CONSTRAINT, CONDITION)

    op.create_check_constraint(CONSTRAINT, "orders", CONDITION, schema="ledger")


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, "orders", type_="check", schema="ledger")
