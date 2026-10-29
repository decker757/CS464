"""The audit log's vocabulary, as this service writes it. [3.4] #12, ADR 0006.

The table and the insert are behind `core/audit.py`; only what differs by design
lives here. `ledger_svc` holds INSERT on `audit.admin_actions` and nothing else.
"""

# Twins: market_service/model/audit.py and auth_service/model/audit.py.

from __future__ import annotations

from enum import StrEnum


class AdminAction(StrEnum):
    """The action types this service appends, namespaced by what was acted on.

    A Python enum rather than a CHECK constraint, so a stale audit schema
    cannot abort a valid admin action. ADR 0006.
    """

    # [3.4] #12. Appended in the payout transaction, never on a repeat. The
    # winner and the three figures in `context`; no label, no reason.
    # DECISIONS.md, "`market.settled` carries the winner and the three figures".
    MARKET_SETTLED = "market.settled"

    # [BE] #221, ADR 0025. Appended in the refunds' transaction, never on a
    # repeat. The trimmed reason in `reason`; the five figures in `context`,
    # whether refunds were cut among them.
    MARKET_VOIDED = "market.voided"

    # [T-8] #326. Appended in the cancel's transaction. The reason, and the
    # order's owner and market in `context`. The owner's own cancel appends
    # nothing, so every entry of this type is an administrator's.
    # DECISIONS.md, "An administrator can cancel any live order, with a reason".
    ORDER_CANCELLED_BY_ADMIN = "order.cancelled_by_admin"
