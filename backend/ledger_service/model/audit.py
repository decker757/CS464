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
