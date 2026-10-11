"""dispute_window_ends_at: when an approval's dispute window ends. [3.3] #11

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-11

Autogenerate wrote the column and its drop; the backfill is hand-written,
because autogenerate cannot see data. No CHECK and no index, so nothing else
here is invisible to it. unit_test/test_migrations.py proves this builds what
the model builds, and has the backfill's own test. ADR 0023.
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
    op.add_column(
        "markets",
        sa.Column("dispute_window_ends_at", sa.DateTime(timezone=True), nullable=True),
        schema="market",
    )
    # Every market approved before this revision gets the default 24-hour
    # window. Written in rather than read from DISPUTE_WINDOW_SECONDS: the
    # migrate step reads DATABASE_URL alone. ADR 0023.
    op.execute(
        "UPDATE market.markets "
        "SET dispute_window_ends_at = approved_at + interval '86400 seconds' "
        "WHERE approved_at IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("markets", "dispute_window_ends_at", schema="market")
