"""The role vocabulary, as it travels in an access token. [F-6] #76

One definition, shared, because this is a wire contract rather than a design
choice: the auth service writes these values into `auth.users.role` and signs
them into the `role` claim, and every other service reads them back. Two copies
drifting apart does not produce two opinions — it produces a service that
cannot recognise authority the token is actually carrying.

That was the risk while this was copied five times. The copies agreed, but
nothing made them agree, and `core/security.py`'s `_read_role` fails closed to
TRADER, so the failure mode was silent: a service that had not heard of a value
would quietly treat a privileged caller as an ordinary one.

Adding a member here reaches every service at once, which is the point. It is
also why adding one is an auth-service decision — that service owns the column
these are stored in.
"""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    """The value stored on `auth.users.role` and carried in the JWT."""

    TRADER = "trader"
    ADMIN = "admin"
