"""Who a user is allowed to be.

A deliberately small slice of [4.4] #16. That ticket splits administration
into MARKET_CREATOR, RESOLVER and SUPER_ADMIN so that creating a market and
resolving it cannot be the same person. None of that is decided here; this
file exists because [1.1] #1 needs a market service to know whether the
caller is an administrator at all, and the only channel it has for that is
the access token.

Adding a member here changes the cross-service contract, because the value
travels in the `role` claim and every service reads it. Keep it in step with
`backend/market_service/core/roles.py`, which holds the reader's copy.
"""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    """The value stored on `auth.users.role` and carried in the JWT."""

    TRADER = "trader"
    ADMIN = "admin"
