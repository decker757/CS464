"""Domain errors.

Plain exceptions with no framework imports, so the service layer can raise them
without knowing HTTP exists. `controller/errors.py` owns the mapping from these
to status codes.
"""

from __future__ import annotations

from dataclasses import dataclass


class MarketError(Exception):
    """Base class for every market domain error."""

    status_code: int = 400
    code: str = "market_error"
    message: str = "Market error."


class NotAuthenticated(MarketError):
    """Missing, malformed or expired access token."""

    status_code = 401
    code = "invalid_token"
    message = "Not authenticated."


class NotAnAdministrator(MarketError):
    """Authenticated, but not carrying the admin role.

    401 and 403 are kept distinct here because they mean different things to
    the frontend: 401 means the session is gone and the user should be sent to
    log in, 403 means the session is fine and this account will never be
    allowed in. Retrying the first can help; retrying the second cannot.
    """

    status_code = 403
    code = "not_an_administrator"
    message = "This action requires an administrator account."


class MarketNotFound(MarketError):
    """Either it does not exist, or it belongs to a different administrator.

    Deliberately one error for both. [1.1] #1 requires a draft be invisible to
    everyone but its creator, and a 403 on someone else's market would confirm
    that market exists, which is most of what a 404 is meant to hide. Same
    reasoning as the auth service's deliberately generic InvalidCredentials.
    """

    status_code = 404
    code = "market_not_found"
    message = "No such market."


@dataclass(frozen=True)
class ValidationProblem:
    """One reason a draft cannot be submitted, addressed to a form field."""

    field: str
    message: str


class DraftIncomplete(MarketError):
    """Submission was refused because the draft does not satisfy every rule.

    Carries the full list rather than the first failure, so the form can mark
    every offending field at once instead of making the admin discover them one
    save at a time.
    """

    status_code = 422
    code = "draft_incomplete"

    def __init__(self, problems: list[ValidationProblem]) -> None:
        self.problems = problems
        self.message = "This market cannot be submitted yet."
        super().__init__(self.message)


class MarketNotEditable(MarketError):
    """An autosave tried to write over an already-submitted market.

    Groundwork for [1.4] #4. A submitted market is complete and validated, and
    a background autosave must never be able to quietly revert it to a draft.
    """

    status_code = 409
    code = "market_not_editable"
    message = (
        "This market has already been submitted. Submit again to change it, "
        "rather than saving it as a draft."
    )
