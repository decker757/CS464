"""Domain errors, free of HTTP. `controller/errors.py` maps them to responses."""

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
    """Authenticated, but not an admin.

    403, not 401: a fresh login helps with a 401 and never with this.
    """

    status_code = 403
    code = "not_an_administrator"
    message = "This action requires an administrator account."


class MalformedCursor(AuditError):
    """The `cursor` parameter did not come from a previous response.

    Refused rather than restarting from the newest page, which would loop a
    client forever.
    """

    status_code = 400
    code = "malformed_cursor"
    message = "Pass back the `next_cursor` from the previous response, unmodified."
