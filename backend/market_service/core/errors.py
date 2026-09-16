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


class IncompleteError(MarketError):
    """A 422 that names every field at fault, rather than only the first.

    The shape, not an error anything raises. Two actions refuse this way — a
    submission or publication whose terms do not pass ([1.1] #1, [1.3] #3), and
    a proposal whose winner or evidence does not ([3.1] #9) — and both are a
    form the administrator is looking at, so both want every offending field
    marked at once instead of one save at a time.

    Sharing a base is what keeps `controller/errors.py` free of a list of which
    errors carry `details`. It attaches them for anything that is one of these,
    so a third action that refuses by field gets the same envelope for free.
    """

    status_code = 422

    DEFAULT_MESSAGE = "This request cannot be accepted yet."

    def __init__(
        self, problems: list[ValidationProblem], *, message: str | None = None
    ) -> None:
        self.problems = problems
        self.message = message or self.DEFAULT_MESSAGE
        super().__init__(self.message)


class DraftIncomplete(IncompleteError):
    """The terms do not satisfy every rule, so this market cannot move on.

    Raised by submission and by publication both, because `service/validation.py`
    is one definition of "ready" and [1.3] #3 asks the same question [1.1] #1
    did. The `code` and the `details` shape are therefore identical on both, and
    only the sentence changes: the fields at fault are the same fields, and a
    frontend that already renders a refused submission renders a refused publish
    with no new branch.
    """

    code = "draft_incomplete"

    DEFAULT_MESSAGE = "This market cannot be submitted yet."


class ProposalIncomplete(IncompleteError):
    """The proposed outcome or its evidence does not satisfy every rule. [3.1] #9

    A separate code from `draft_incomplete` rather than a reuse of it, because
    the fields it names are not the market's terms — `winning_outcome_id`,
    `evidence_url` and `evidence_note` exist only on the proposal request, and a
    form that mapped them onto the create form's inputs would find nothing to
    mark. Same envelope, different form.
    """

    code = "proposal_incomplete"

    DEFAULT_MESSAGE = "This outcome cannot be proposed yet."


class MarketNotEditable(MarketError):
    """An autosave tried to write over an already-submitted market.

    Groundwork for [1.4] #4. A submitted market is complete and validated, and
    a background autosave must never be able to quietly revert it to a draft.

    Distinct from MarketAlreadyOpen, and the difference is the remedy the
    message offers. A submitted market can still be changed, by submitting it
    again; an open one cannot be changed at all.
    """

    status_code = 409
    code = "market_not_editable"
    message = (
        "This market has already been submitted. Submit again to change it, "
        "rather than saving it as a draft."
    )


class MarketNotSubmitted(MarketError):
    """Publication was asked for on a market that is still a draft. [1.3] #3.

    Publishing is a second decision rather than a shortcut through the first.
    A draft has never been through the submission gate, so the administrator
    has not yet said the terms are final — and a draft is the one state where
    the autosave is still overwriting the row every three seconds, which is not
    a thing to make tradeable.

    409 rather than 422: nothing about the request is malformed and nothing
    about the terms is necessarily wrong. The market is in the wrong state, and
    the admin can fix that by pressing submit. ADR 0008.
    """

    status_code = 409
    code = "market_not_submitted"
    message = (
        "Submit this market before publishing it. Publishing is a separate "
        "decision from finalising the terms."
    )


class MarketClosed(MarketError):
    """A write arrived for a market that has stopped trading. [F-4] #44.

    Distinct from MarketAlreadyOpen for the same reason MarketNotEditable is:
    the remedy differs. An open market is frozen but still live, and the admin
    should reload to see it. A closed one is finished — there is nothing to
    publish, nothing to edit, and the next thing that happens to it is [3.1]
    #9 proposing an outcome.

    Reachable in ordinary use despite looking like an edge case. A form left
    open behind the publish button keeps autosaving every three seconds, and
    [2.3] #7 will let an administrator close a market out from under exactly
    that form.
    """

    status_code = 409
    code = "market_closed"
    message = (
        "This market has closed and is no longer trading. Its terms are final "
        "and it cannot be reopened."
    )


class MarketAlreadyOpen(MarketError):
    """A write arrived for a market that traders can already see. [1.3] #3.

    One error for two callers, because they are the same situation. A second
    publish has nothing left to do, and an autosave from a form still open
    behind the publish button must not touch terms that traders are pricing
    against. Both learn that this market is finished, and both should reload.

    This is the guard that makes publication one-way. [1.4] #4 widens it into a
    general rule about non-draft markets; until then this narrow version is
    what stops the three-second timer quietly reverting a live market.
    """

    status_code = 409
    code = "market_already_open"
    message = (
        "This market is already open to traders. Its terms are frozen, and "
        "publishing it again would do nothing."
    )


class MarketNotClosed(MarketError):
    """An outcome was proposed for a market that is still running. [3.1] #9.

    The ticket's first acceptance criterion, as a refusal. Proposing an outcome
    for a market that is still accepting trades would let an administrator
    announce the answer to a question people are still betting on, which is the
    same failure `service/validation.py`'s close-before-resolution rule exists
    to prevent — stated there as "someone can bet on a result they already
    know".

    409 rather than 422 or 404: nothing about the request is malformed, the
    market is real and it is the caller's, and the remedy is to wait. For a
    market whose closing time has just passed, the wait is one sweep — see
    ADR 0011 for why the status column lags the clock by a few seconds and why
    this gate reads the column anyway.
    """

    status_code = 409
    code = "market_not_closed"
    message = (
        "An outcome can only be proposed for a market that has closed. This "
        "one has not closed yet."
    )


class MarketPendingResolution(MarketError):
    """A proposal or a write arrived for a market already awaiting one. [3.1] #9.

    One error for two callers, the way MarketAlreadyOpen is, because they are
    the same situation and take the same remedy: reload, and stop offering the
    control. A second proposal has nothing to add — the first is what a second
    administrator is being asked to agree to — and a save must not change the
    terms that proposal was made against.

    Distinct from MarketClosed even though both describe a market that has
    stopped trading, because only one of them has a proposal on it. An
    administrator looking at a closed market should be offered the propose
    control; one looking at this market should be shown whose proposal is
    waiting.
    """

    status_code = 409
    code = "market_pending_resolution"
    message = (
        "An outcome has already been proposed for this market, and it is "
        "waiting on a second administrator."
    )
