"""The trade history is the ledger presented. [T-5] #25

No new table and no new column: every figure on a history row is read or
derived from what [F-1] #41 and the trades already store. And the
`balance_after` contract promises a figure on screen never moves, which #187's
write path keeps.
"""

from __future__ import annotations

from core.database import Base
from model.entities import Entry, Transaction
from model.schemas import LedgerEntryOut


def test_the_history_adds_no_table_and_no_column() -> None:
    """A stored `balance_after`, a price column or a history table would be a
    second source of truth for figures derived from the entries (ADR 0009)."""
    assert {table.name for table in Base.metadata.tables.values()} == {
        "accounts",
        "transactions",
        "entries",
        "market_books",
        "market_outcomes",
        "positions",
    }
    assert {column.name for column in Entry.__table__.columns} == {
        "id",
        "transaction_id",
        "account_id",
        "amount",
        "created_at",
    }
    assert {column.name for column in Transaction.__table__.columns} == {
        "id",
        "kind",
        "idempotency_key",
        "request_fingerprint",
        "occurred_at",
        "context",
    }


def test_balance_after_s_description_promises_a_figure_on_screen_does_not_move() -> None:
    """The OpenAPI contract, restored by #187: entries list in commit order."""
    description = LedgerEntryOut.model_fields["balance_after"].description or ""

    assert "cannot change a figure already on the screen" in description
    assert "Known limitation" not in description
