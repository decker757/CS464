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
index and builds it again in one transaction. The DROP needs an ACCESS
EXCLUSIVE lock on `ledger.entries`: it first waits behind every open
transaction that has touched the table, every new query on the table queues
behind it while it waits, and it holds the lock until the commit, so every
balance and trade waits for the build too. The migrate step sets no
`lock_timeout`, so one long transaction stretches that stall for as long as it
stays open. The downgrade takes the same lock and does the same rebuild.
Only a manual `alembic downgrade` reaches it, since a failed release is fixed
forward (ADR 0026). The build took about 30 ms on 110,000 entries on a
laptop, and it grows a little faster than the row count.

When that stall is too long, a concurrent rebuild is the answer, and this
revision cannot run one. `shared/migrating.py` runs every revision inside one
outer `engine.begin()`, so Alembic sees an external transaction and
`op.get_context().autocommit_block()` is not available. It fails its
assertion before anything is built. There are two real routes:

- Rebuild by hand first. In psql as `ledger_svc`, run `CREATE INDEX
  CONCURRENTLY` under a temporary name, then `DROP INDEX CONCURRENTLY` the
  old index and rename the new one into place. Then deploy a revision that
  only records the change.
- Change the runner first, to `transaction_per_migration` with no outer
  `begin()`. That is an ADR 0020 decision, not a migration detail.

A `CREATE INDEX CONCURRENTLY` that fails leaves an INVALID index behind. Drop
it before you retry.

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
