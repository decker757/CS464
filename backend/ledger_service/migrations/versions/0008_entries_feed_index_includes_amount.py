"""entries: the feed index includes amount, so a balance sum reads only the index. #642

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-05

A balance is `SUM(amount)` over one account's entries, derived on every read
(ADR 0009), and the trade path takes it under the account lock.
`ix_ledger_entries_account_feed` already finds those entries; carrying
`amount` as an INCLUDE column lets Postgres answer the sum from the index alone
for every heap page the visibility map marks all-visible. Entries newer than
the last vacuum still cost a heap fetch, so the gain is on an account's long
history. The key columns and their order are unchanged, so the history page and
the newest-entry probe read it exactly as before.

Postgres cannot add an INCLUDE column to an existing index, so this drops the
index and builds it again, both inside the migration's one transaction. The
DROP takes an ACCESS EXCLUSIVE lock on `ledger.entries` and keeps it until the
commit, so every read and write of the table, every balance and trade
included, waits for the build. That is acceptable at production's size today.
At a size where it is not, build the new index with `CREATE INDEX
CONCURRENTLY` under a temporary name, `DROP INDEX CONCURRENTLY` the old one and
rename the new one into place. CONCURRENTLY cannot run inside a transaction
block, so those statements go in an `op.get_context().autocommit_block()`,
outside the migration's transaction. ADR 0020.

The migration changes no column, and the old release reads the rebuilt index
the same way it read the old one, so it works with the release before it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_ledger_entries_account_feed"
_KEY = ["account_id", sa.text("created_at DESC"), sa.text("id DESC")]


def upgrade() -> None:
    op.drop_index(_INDEX, table_name="entries", schema="ledger")
    op.create_index(
        _INDEX,
        "entries",
        _KEY,
        unique=False,
        schema="ledger",
        postgresql_include=["amount"],
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="entries", schema="ledger")
    op.create_index(_INDEX, "entries", _KEY, unique=False, schema="ledger")
