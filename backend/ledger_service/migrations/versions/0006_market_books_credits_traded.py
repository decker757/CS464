"""market_books: credits_traded, backfilled from each pool's trades. [X-1] #447

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-21

The column a trade raises under the book lock, DECISIONS.md, "`credits_traded`
is a counter on the book, raised in `write_trade` under the book lock". Every
trade posts one leg on its market's pool of exactly the credits it moved, so
summing those legs, unsigned, is the figure every existing book would hold had
the column always been there. A book nobody traded keeps the default of zero.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "market_books",
        sa.Column(
            "credits_traded",
            sa.Numeric(precision=18, scale=4),
            server_default=sa.text("0"),
            nullable=False,
        ),
        schema="ledger",
    )
    op.execute(
        """
        UPDATE ledger.market_books AS book
        SET credits_traded = traded.credits
        FROM (
            SELECT entry.account_id, sum(abs(entry.amount)) AS credits
            FROM ledger.entries AS entry
            JOIN ledger.transactions AS movement
              ON movement.id = entry.transaction_id
            WHERE movement.kind IN ('trade_buy', 'trade_sell')
            GROUP BY entry.account_id
        ) AS traded
        WHERE traded.account_id = book.pool_account_id
        """
    )


def downgrade() -> None:
    op.drop_column("market_books", "credits_traded", schema="ledger")
