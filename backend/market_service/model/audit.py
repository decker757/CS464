"""The audit log's vocabulary, as this service writes it. [4.3] #15, ADR 0006.

The table and the insert are behind `core/audit.py`; only what differs by design
lives here. `audit.admin_actions` is not this service's table: `market_svc`
holds INSERT on it and nothing else.
"""

from __future__ import annotations

from enum import StrEnum


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
