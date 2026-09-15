"""Domain errors.

Plain exceptions with no framework imports, so the service layer can raise them
without knowing HTTP exists. `controller/errors.py` owns the mapping from these
to status codes.
"""

from __future__ import annotations


class AuditError(Exception):
    """Base class for every audit domain error."""

    status_code: int = 400
    code: str = "audit_error"
    message: str = "Audit error."


class NotAuthenticated(AuditError):
    """Missing, malformed or expired access token."""

    status_code = 401
    code = "invalid_token"
    message = "Not authenticated."


class NotAnAdministrator(AuditError):
    """Authenticated, but not carrying the admin role.

    The log records what administrators did to other people's accounts and
    markets, so reading it is itself an administrator's privilege. Kept
    distinct from 401 for the same reason as the market service: 401 means the
    session is gone and retrying after a login helps, 403 means it never will.
    """

    status_code = 403
    code = "not_an_administrator"
    message = "This action requires an administrator account."


class MalformedCursor(AuditError):
    """The `cursor` parameter did not come from a previous response.

    Its contents are this service's business, so a client should only ever
    echo back what `next_cursor` gave it. Saying so explicitly beats silently
    restarting from the newest page, which would loop forever.
    """

    status_code = 400
    code = "malformed_cursor"
    message = "Pass back the `next_cursor` from the previous response, unmodified."
