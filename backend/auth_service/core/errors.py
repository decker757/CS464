"""Domain errors.

Plain exceptions with no framework imports, so the service layer can raise
them without knowing HTTP exists. `controller/errors.py` owns the mapping from
these to status codes.
"""

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
    #: Which inputs caused this error. The handler renders it as the envelope's
    #: `details`, so the frontend can mark the offending fields instead of
    #: parsing `message`. Empty when the error is not attributable to an input,
    #: which is the normal case for a deliberately generic error.
    problems: Sequence[FieldProblem] = ()


class DuplicateUser(AuthError):
    """[A-1] #29 - username or email is already taken.

    Carries every clashing field rather than the first, so the form can mark
    them all at once instead of making the user discover them one submission
    at a time. `fields` is empty when a concurrent registration beat us to the
    insert, because the database reports only that a unique constraint failed,
    not which one.
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
