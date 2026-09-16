"""The audit log, as a writer sees it. [4.3] #15

`audit.admin_actions` is not this service's table. `sql/02-schemas.sql` creates
it and the superuser owns it; `market_svc` holds INSERT on it and nothing else.
It cannot SELECT the log, cannot UPDATE or DELETE a row, and cannot alter the
table. See docs/adr/0006-audit-log-write-path.md.

Two details below are load-bearing rather than stylistic.

**This table is deliberately NOT on `core.database.Base.metadata`.** Everything
mapped there is created by `create_all` at startup and dropped and recreated by
`unit_test/conftest.py` on every test. Either against this table would fail —
there is no CREATE or DROP grant — and the service would die at boot. A
standalone `Table` on its own `MetaData` gives the columns a name to insert
against without enrolling them in anything that issues DDL.

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
SOURCE_SERVICE = "market_service"

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

    Namespaced by the entity acted on, so the log reads sensibly when the auth
    service's `user.suspended` and the ledger's `payout.settled` land beside
    these. The vocabulary lives here rather than as a CHECK constraint in SQL:
    the audit INSERT commits with the action it records, so a constraint the
    database could reject on is a way for a stale audit schema to abort a
    perfectly good admin action. sql/02-schemas.sql explains that at length.

    Notice which action is absent. A draft autosave is not here and must never
    be: the form saves every three seconds, so logging it would bury every real
    action under thousands of keystroke records within one sitting. The log is
    for decisions, not for typing.
    """

    MARKET_SUBMITTED = "market.submitted"

    # [1.3] #3. Recorded separately from the submission rather than folded into
    # it, because they are two decisions and only the second one exposed
    # anything to a trader. "Who made this market tradeable, and on what terms"
    # is the question the log will actually be asked once money is moving, and
    # a market can sit submitted for a week before anybody answers it.
    MARKET_PUBLISHED = "market.published"

    # [3.1] #9. An administrator named a winning outcome and attached the
    # evidence for it.
    #
    # The market row carries the same facts and is not a substitute for this
    # entry, because [3.2] #10's rejection clears those columns and sends the
    # market back to CLOSED. After that the only record that a proposal was
    # ever made — and of who made it, and on what evidence — is this one. The
    # story asks for the decision to be documented, and a column that a later
    # action overwrites does not document anything.
    MARKET_OUTCOME_PROPOSED = "market.outcome_proposed"
