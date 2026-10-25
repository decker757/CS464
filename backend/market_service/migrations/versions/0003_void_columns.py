"""voided_at and void_reason: when a market was voided, and why. [BE] #221

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-25

Two nullable columns and their drops, the operations autogenerate writes for
them. No CHECK, no index and no backfill: no market is voided before this
revision, and no database rule here reads a status string. So nothing here is
invisible to autogenerate, and unit_test/test_migrations.py proves this
builds what the model builds. ADR 0025.
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
    op.add_column(
        "markets",
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        schema="market",
    )
    op.add_column(
        "markets",
        sa.Column("void_reason", sa.Text(), nullable=True),
        schema="market",
    )


def downgrade() -> None:
    op.drop_column("markets", "void_reason", schema="market")
    op.drop_column("markets", "voided_at", schema="market")
