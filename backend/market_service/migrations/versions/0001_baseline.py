"""Baseline: the market schema as create_all built it before #75. [F-5] #75

Revision ID: 0001
Revises:
Create Date: 2026-10-04

Written from the models and reviewed line by line; unit_test/test_migrations.py
proves it builds exactly what they build. Folds in what sql/migrations/0001 and
0003-0006 added by hand; each is marked where it lands. `status` is a
non-native Enum: a varchar(24) with no CHECK, so its value list is
documentation, not a constraint.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "markets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("creator_id", sa.Uuid(), nullable=False),
        sa.Column("draft_key", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "submitted",
                "open",
                "closed",
                "pending_resolution",
                "approved",
                name="market_status",
                native_enum=False,
                length=24,
            ),
            server_default="draft",
            nullable=False,
        ),
        sa.Column("question", sa.String(length=500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("close_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_criteria", sa.Text(), nullable=True),
        # sql/migrations/0001, [1.2] #2.
        sa.Column("liquidity_b", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("seed_subsidy", sa.Numeric(precision=18, scale=4), nullable=True),
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
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        # sql/migrations/0003, [1.3] #3.
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        # sql/migrations/0004, [F-4] #44, with ix_markets_due_close below.
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        # sql/migrations/0006, [3.2] #10.
        sa.Column("proposal_id", sa.Uuid(), nullable=True),
        # sql/migrations/0005, [3.1] #9.
        sa.Column("proposed_outcome_id", sa.Uuid(), nullable=True),
        sa.Column("proposed_by_id", sa.Uuid(), nullable=True),
        sa.Column("proposed_by_username", sa.String(length=32), nullable=True),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("proposal_evidence_url", sa.String(length=2048), nullable=True),
        sa.Column("proposal_evidence_note", sa.Text(), nullable=True),
        # sql/migrations/0006, [3.2] #10.
        sa.Column("approved_by_id", sa.Uuid(), nullable=True),
        sa.Column("approved_by_username", sa.String(length=32), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("creator_id", "draft_key", name="uq_markets_creator_draft_key"),
        schema="market",
    )
    op.create_index(
        op.f("ix_market_markets_creator_id"), "markets", ["creator_id"], unique=False, schema="market"
    )
    op.create_index(
        op.f("ix_market_markets_status"), "markets", ["status"], unique=False, schema="market"
    )
    op.create_index(
        "ix_markets_creator_updated",
        "markets",
        ["creator_id", "updated_at"],
        unique=False,
        schema="market",
    )
    # compare_metadata cannot see this predicate; test_migrations.py asserts it.
    op.create_index(
        "ix_markets_due_close",
        "markets",
        ["close_time"],
        unique=False,
        schema="market",
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_table(
        "market_outcomes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("market_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.ForeignKeyConstraint(["market_id"], ["market.markets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("market_id", "position", name="uq_outcome_position"),
        schema="market",
    )
    op.create_index(
        op.f("ix_market_market_outcomes_market_id"),
        "market_outcomes",
        ["market_id"],
        unique=False,
        schema="market",
    )
    op.create_table(
        "resolution_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("market_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=True),
        sa.ForeignKeyConstraint(["market_id"], ["market.markets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("market_id", "position", name="uq_source_position"),
        schema="market",
    )
    op.create_index(
        op.f("ix_market_resolution_sources_market_id"),
        "resolution_sources",
        ["market_id"],
        unique=False,
        schema="market",
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_market_resolution_sources_market_id"),
        table_name="resolution_sources",
        schema="market",
    )
    op.drop_table("resolution_sources", schema="market")
    op.drop_index(
        op.f("ix_market_market_outcomes_market_id"), table_name="market_outcomes", schema="market"
    )
    op.drop_table("market_outcomes", schema="market")
    op.drop_index("ix_markets_due_close", table_name="markets", schema="market")
    op.drop_index("ix_markets_creator_updated", table_name="markets", schema="market")
    op.drop_index(op.f("ix_market_markets_status"), table_name="markets", schema="market")
    op.drop_index(op.f("ix_market_markets_creator_id"), table_name="markets", schema="market")
    op.drop_table("markets", schema="market")
