"""The audit log's vocabulary, as this service writes it. [4.3] #15, [4.4] #16

The table and the insert are behind `core/audit.py`; only what differs by design
lives here. `auth_svc` holds INSERT on `audit.admin_actions` and nothing else.
ADR 0006.
"""

from __future__ import annotations

from enum import StrEnum


class AdminAction(StrEnum):
    """The action types this service appends, namespaced by what was acted on.

    Here rather than a CHECK in SQL, which could abort the action it records.
    ADR 0006. Never add a login: the log is for decisions about other people,
    not a user's own traffic.
    """

    USER_ROLE_CHANGED = "user.role_changed"
