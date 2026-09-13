"""Domain errors.

Plain exceptions with no framework imports, so the service layer can raise
them without knowing HTTP exists. `controller/errors.py` owns the mapping from
these to status codes.
"""


class AuthError(Exception):
    """Base class for every auth domain error."""

    status_code: int = 400
    code: str = "auth_error"
    message: str = "Authentication error."


class DuplicateUser(AuthError):
    """[A-1] #29 - username or email is already taken."""

    status_code = 409
    code = "duplicate_user"

    def __init__(self, field: str) -> None:
        self.field = field
        self.message = f"That {field} is already registered."
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
