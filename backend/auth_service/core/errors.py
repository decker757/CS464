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


class NotAnAdministrator(AuthError):
    """[4.4] #16 - authenticated, but not carrying the admin role.

    Same code and message as the market service's error of the same name, so
    the frontend handles one shape for "you are signed in and still may not do
    this" across both services.
    """

    status_code = 403
    code = "not_an_administrator"
    message = "This action requires an administrator account."


class UserNotFound(AuthError):
    """[4.4] #16 - no user with that id.

    Not deliberately vague, unlike InvalidCredentials. The caller is already a
    proven administrator, so telling them an id does not exist reveals nothing
    they could not learn from the user list they are entitled to read.
    """

    status_code = 404
    code = "user_not_found"
    message = "No user with that id."


class CannotChangeOwnRole(AuthError):
    """[4.4] #16 - an administrator may not change their own role.

    One line, and it is the invariant that keeps the administrator set from
    emptying: a change must come from an administrator and may not target
    themselves, so the last remaining administrator has no legal target whose
    demotion would leave zero. docs/adr/0007-admin-tiers-and-role-changes.md
    explains why that matters more than the self-footgun it also prevents.
    """

    status_code = 403
    code = "cannot_change_own_role"
    message = "An administrator cannot change their own role."


class MalformedCursor(AuthError):
    """[4.1] #13 - the `cursor` parameter did not come from a previous response.

    Its contents are this service's business, so a client should only ever echo
    back what `next_cursor` gave it. Saying so explicitly beats silently
    restarting from the newest page, which would loop forever.

    Same code and message as the audit and ledger services', because it is the
    same cursor format and the frontend should not learn three spellings of one
    mistake.
    """

    status_code = 400
    code = "malformed_cursor"
    message = "Pass back the `next_cursor` from the previous response, unmodified."


class LastAdministrator(AuthError):
    """[4.4] #16 - demoting this user would leave the system with no administrator.

    The self-change rule alone does not prevent this. Two administrators can
    demote each other at the same instant: each targets somebody else, so each
    passes that check, and both transactions commit. Sequentially the set can
    only shrink to one; concurrently it can reach zero, and nothing short of a
    manual UPDATE gets it back.

    A conflict rather than a forbidden action. The caller is entitled to do
    this and the request is well formed; it is the state of the system that
    refuses, and a moment later — once somebody else is promoted — the very
    same request would succeed.
    """

    status_code = 409
    code = "last_administrator"
    message = "This is the only administrator. Promote another one first."
