"""The audit log, as a writer sees it. [4.3] #15, ADR 0006.

`audit.admin_actions` is not this service's table: `market_svc` holds INSERT on
it and nothing else. Two details are load-bearing:

- It must never join `core.database.Base.metadata`. `create_all` and the test
  rebuild would issue DDL against it, which this role may not, and the service
  would die at boot. Hence a standalone `Table` on its own `MetaData`.
- The id is generated in Python. A server default would need RETURNING, which
  needs SELECT, which this role must not have.
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

# What this service reports as `source_service`.
SOURCE_SERVICE = "market_service"

# Separate from Base.metadata on purpose; see the module docstring.
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
    """The action types this service appends, namespaced by what was acted on.

    A Python enum rather than a CHECK constraint, so a stale audit schema
    cannot abort a valid admin action. ADR 0006. Autosave is deliberately
    absent and must stay so: the log is for decisions, not keystrokes.
    """

    MARKET_SUBMITTED = "market.submitted"

    # [1.3] #3. Separate from submission: only publication puts the terms in
    # front of traders. ADR 0008.
    MARKET_PUBLISHED = "market.published"

    # [2.3] #7. The only close in the log: the clock is not an actor, so an
    # entry here always means a human intervened. The justification goes in
    # `reason`. ADR 0014.
    MARKET_CLOSED_EARLY = "market.closed_early"

    # [3.1] #9. Outlives the proposal columns, which a rejection clears.
    # ADR 0013.
    MARKET_OUTCOME_PROPOSED = "market.outcome_proposed"

    # [3.2] #10. Under the approver, with no `reason`, and carrying the
    # proposal it approved. ADR 0016.
    MARKET_OUTCOME_APPROVED = "market.outcome_approved"

    # [3.2] #10. The reason in `reason`, and the same eight `context` keys as
    # an approval, snapshotted before the rejection cleared them. ADR 0016.
    MARKET_OUTCOME_REJECTED = "market.outcome_rejected"
