"""The reader's copy of the auth service's role vocabulary.

Deliberately duplicated rather than imported, for the reasons ADR 0003 gives:
services in this repository share no code and no schema, and a package that
both import would be the first thread tying them together. The coupling that
exists is the `role` claim in the access token, which is a wire contract.

Keep in step with `backend/auth_service/core/roles.py`. A value this file has
never heard of is treated as TRADER, so the two drifting apart costs authority
rather than granting it.
"""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    TRADER = "trader"
    ADMIN = "admin"
