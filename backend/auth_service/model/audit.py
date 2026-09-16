"""The audit log, as a writer sees it. [4.3] #15, [4.4] #16

`audit.admin_actions` is not this service's table. `sql/02-schemas.sql` creates
it and the superuser owns it; `auth_svc` holds INSERT on it and nothing else.
It cannot SELECT the log, cannot UPDATE or DELETE a row, and cannot alter the
table. See docs/adr/0006-audit-log-write-path.md.

This file is a copy of `market_service/model/audit.py` with a different
`SOURCE_SERVICE` and a different vocabulary. Keep the two in step.

ADR 0006 predicted the copy and attributed it to the Docker build context.
**That blocker is gone** — [F-6] #76 moved the contexts to `backend/` and
`backend/shared/` exists — so the copy is now a choice rather than a
constraint, and it is still the right one for the moment. What differs between
the two files is `AdminAction`: this service logs things done to users, the
market service logs things done to markets, and the enum is the vocabulary a
reader filters the feed on. Sharing the `Table` while each service kept its own
enum is a real option and a small one; it is ADR 0006's call to make, not
something to fold into a refactor of `core`.

Two details below are load-bearing rather than stylistic.

**This table is deliberately NOT on `core.database.Base.metadata`.** Everything
mapped there is created by `create_all` at startup and truncated by
`unit_test/conftest.py` on every test. Either against this table would fail —
there is no CREATE or DROP grant, and no DELETE or TRUNCATE either — and the
service would die at boot. A standalone `Table` on its own `MetaData` gives the
columns a name to insert against without enrolling them in anything that
issues DDL.

**The id is generated here rather than by the database.** A server-side default
would have to be read back with RETURNING, and RETURNING is a read: it needs
SELECT, which this role does not have and should not be given, because SELECT
is what would let this service read another service's actions.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import (
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB

AUDIT_SCHEMA = "audit"

# The name this service reports as `source_service`, so an entry can be traced
# back to the process that wrote it.
SOURCE_SERVICE = "auth_service"

# Separate from Base.metadata on purpose. Read the module docstring before
# moving it.
audit_metadata = MetaData(schema=AUDIT_SCHEMA)

admin_actions = Table(
    "admin_actions",
    audit_metadata,
    Column("id", Uuid, primary_key=True),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("actor_id", Uuid, nullable=False),
    Column("actor_username", String(32), nullable=False),
    Column("actor_role", String(16), nullable=False),
    Column("action_type", String(64), nullable=False),
    Column("target_type", String(32), nullable=False),
    Column("target_id", Uuid, nullable=True),
    Column("target_label", Text, nullable=True),
    Column("reason", Text, nullable=True),
    Column("context", JSONB, nullable=True),
    Column("source_service", String(32), nullable=False),
)


class AdminAction(StrEnum):
    """The action types this service appends.

    Namespaced by the entity acted on, so the log reads sensibly beside the
    market service's `market.submitted` and the ledger's eventual
    `payout.settled`. The vocabulary lives here rather than as a CHECK
    constraint in SQL: the audit INSERT commits with the action it records, so
    a constraint the database could reject on is a way for a stale audit schema
    to abort a perfectly good admin action. sql/02-schemas.sql explains that at
    length.

    Notice which actions are absent. A login is not here and must never be:
    people log in all day, and burying one privilege grant under a thousand
    session records is the same failure as logging draft autosaves. The log is
    for decisions about other people, not for a user's own traffic.

    [4.2] #14 adds `user.suspended` and `user.unsuspended` alongside.
    """

    USER_ROLE_CHANGED = "user.role_changed"
