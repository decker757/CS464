"""orders: filled_quantity and filled_value move together. [T-6] #26, #397

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-19

Hand-written, because autogenerate cannot see a CHECK constraint (ADR 0020) and
so will never offer this one; unit_test/test_migrations.py compares the Postgres
catalog and holds the revision to the model.

A backstop rather than a fix. The invariant already holds by construction:
`core/pricing.py::quantize_cost` refuses a quantized zero on both sides —
`CostBelowTick` on a buy, `ProceedsBelowTick` on a sell — so no fill moves
quantity without moving value by at least one tick, and this constraint rejects
no row the code can currently produce. What it buys is loudness. The reservation
is `SUM(budget - filled_value)` over live orders
(`service/reservations.py::reserved_query_of`), so a cascade that ever advanced
one of the two columns alone would hold a trader's own credits twice and surface
as `insufficient_funds` in balance code that is correct — the expensive kind of
hunt.

The upgrade counts the rows that would violate it and says so before the ALTER,
rather than assuming there are none: "there should be none" is exactly the
reason the constraint is wanted.
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
            "CHECK. A buy like that reserves credits its fills have already spent: "
            "reconcile the rollups against ledger.entries, which carry order_id, "
            "then run the step again. This migration has changed nothing."
        )
    logger.info("ledger.orders: no row violates %s (%s); adding it.", CONSTRAINT, CONDITION)

    op.create_check_constraint(CONSTRAINT, "orders", CONDITION, schema="ledger")


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, "orders", type_="check", schema="ledger")
