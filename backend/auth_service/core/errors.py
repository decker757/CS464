"""Domain errors, free of HTTP. `controller/errors.py` maps them to responses."""

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class FieldProblem:
    """One reason a request was refused, addressed to a form field."""

    field: str
    message: str


class AuthError(Exception):
    """Base class for every auth domain error."""

    status_code: int = 400
    code: str = "auth_error"
    message: str = "Authentication error."
    #: The inputs at fault, rendered as the envelope's `details`. Empty for a
    #: deliberately generic error.
    problems: Sequence[FieldProblem] = ()


class DuplicateUser(AuthError):
    """[A-1] #29 - username or email is already taken.

    Names every clashing field, so the form marks them all at once. `fields`
    may be empty after a lost race whose winner has since been deleted.
    """

    status_code = 409
    code = "duplicate_user"

    def __init__(self, fields: Sequence[str] = ()) -> None:
        self.problems = tuple(
            FieldProblem(field=field, message=f"That {field} is already registered.")
            for field in fields
        )
        self.message = (
            f"That {' and '.join(fields)} {'is' if len(fields) == 1 else 'are'} "
            "already registered."
            if fields
            else "That username or email is already registered."
        )
        super().__init__(self.message)


class InvalidCredentials(AuthError):
    """[A-2] #30 - deliberately generic, never reveals whether the account exists."""

    status_code = 401
    code = "invalid_credentials"
    message = "Incorrect username or password."


class AccountSuspended(AuthError):
    """[A-2] #30 - suspended accounts get a clear, distinct message."""

    status_code = 403
    code = "account_suspended"
    message = "This account is suspended. Contact an administrator."


class InvalidToken(AuthError):
    """[A-3] #31 - missing, malformed, expired, or revoked token."""

    status_code = 401
    code = "invalid_token"
    message = "Not authenticated."


class NotAnAdministrator(AuthError):
    """[4.4] #16 - authenticated, but not an admin.

    Same code and message as the market service's, so the frontend handles one.
    """

    status_code = 403
    code = "not_an_administrator"
    message = "This action requires an administrator account."


class UserNotFound(AuthError):
    """[4.4] #16 - no user with that id.

    Not vague like InvalidCredentials: an admin can read the user list anyway.
    """

    status_code = 404
    code = "user_not_found"
    message = "No user with that id."


class CannotChangeOwnRole(AuthError):
    """[4.4] #16 - an administrator may not change their own role.

    One of the two rules that keep the administrator set from emptying. ADR 0007.
    """

    status_code = 403
    code = "cannot_change_own_role"
    message = "An administrator cannot change their own role."


class MalformedCursor(AuthError):
    """[4.1] #13 - the `cursor` parameter did not come from a previous response.

    Refused rather than restarting from page one, which would loop a client
    forever. Same code and message as the audit and ledger services'.
    """

    status_code = 400
    code = "malformed_cursor"
    message = "Pass back the `next_cursor` from the previous response, unmodified."


class LastAdministrator(AuthError):
    """[4.4] #16 - demoting this user would leave no administrator.

    Reachable only when two admins demote each other at once. ADR 0007. A 409,
    not a 403: the state refuses, and the same request succeeds once somebody
    else is promoted.
    """

    status_code = 409
    code = "last_administrator"
    message = "This is the only administrator. Promote another one first."
