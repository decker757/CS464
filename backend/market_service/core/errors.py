"""Domain errors, one per thing the client can do about it.

No framework imports, so `service/` raises them without knowing HTTP exists;
`controller/errors.py` turns them into responses.
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

    403, not 401: the session is fine, and retrying will not help. The role
    rides in the token, so a user promoted since logging in must log in again.
    """

    status_code = 403
    code = "not_an_administrator"
    message = "This action requires an administrator account."


class MarketNotFound(MarketError):
    """Either it does not exist, or the caller may not see it.

    One error for both: a 403 would confirm that someone else's draft exists,
    which [1.1] #1 forbids. That holds for every creator-scoped read. The
    unscoped writes (`close_early` and the two decisions, through `get_any`)
    deliberately answer another administrator's unpublished market with a 409
    instead. ADR 0016.
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
    """A 422 that names every field at fault, not only the first. Never raised itself.

    `controller/errors.py` attaches `details` to any subclass, so a new
    refusal by field gets the envelope with no change there. ADR 0013.
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
    """The terms do not satisfy every rule. [1.1] #1, [1.3] #3.

    Raised by submission and publication alike, with the same code and
    `details`; only the sentence differs. ADR 0008.
    """

    code = "draft_incomplete"

    DEFAULT_MESSAGE = "This market cannot be submitted yet."


class ProposalIncomplete(IncompleteError):
    """The proposed outcome or its evidence does not satisfy every rule. [3.1] #9

    Its own code because it names fields only the propose form has. ADR 0013.
    """

    code = "proposal_incomplete"

    DEFAULT_MESSAGE = "This outcome cannot be proposed yet."


class CloseIncomplete(IncompleteError):
    """The reason given for closing a market early is not usable. [2.3] #7, ADR 0014."""

    code = "close_incomplete"

    DEFAULT_MESSAGE = "This market cannot be closed without a reason."


class RejectionIncomplete(IncompleteError):
    """The reason given for rejecting a proposed outcome is not usable. [3.2] #10

    Not `close_incomplete`, though the field and floor match today: a
    different form, answering a different question. ADR 0016.
    """

    code = "rejection_incomplete"

    DEFAULT_MESSAGE = "This proposal cannot be rejected without a reason."


class MarketNotEditable(MarketError):
    """An autosave tried to write over an already-submitted market. ADR 0004.

    Not `MarketAlreadyOpen`: a submitted market can still change, by
    submitting again. ADR 0008.
    """

    status_code = 409
    code = "market_not_editable"
    message = (
        "This market has already been submitted. Submit again to change it, "
        "rather than saving it as a draft."
    )


class MarketNotSubmitted(MarketError):
    """Publication was asked for on a market that is still a draft. [1.3] #3.

    409, not 422: the market is in the wrong state, and submitting fixes it.
    ADR 0008.
    """

    status_code = 409
    code = "market_not_submitted"
    message = (
        "Submit this market before publishing it. Publishing is a separate "
        "decision from finalising the terms."
    )


class MarketClosed(MarketError):
    """A write arrived for a market that has stopped trading. [F-4] #44.

    Not `MarketAlreadyOpen`: this market is finished, and its next step is a
    proposed outcome. Also an early close's refusal whether or not the sweep
    has written CLOSED yet. ADR 0014.
    """

    status_code = 409
    code = "market_closed"
    message = (
        "This market has closed and is no longer trading. Its terms are final "
        "and it cannot be reopened."
    )


class MarketAlreadyOpen(MarketError):
    """A second publish or a save arrived for a market traders can see. [1.3] #3.

    The guard that makes publication one way. Both callers should reload.
    ADR 0008.
    """

    status_code = 409
    code = "market_already_open"
    message = (
        "This market is already open to traders. Its terms are frozen, and "
        "publishing it again would do nothing."
    )


class MarketNotClosed(MarketError):
    """An outcome was proposed for a market that is not CLOSED. [3.1] #9.

    409. For a published market the remedy is to wait, at most one sweep
    once the close time has passed. Waiting does not help a draft or a
    submitted market, which never closes. ADR 0013.
    """

    status_code = 409
    code = "market_not_closed"
    message = (
        "An outcome can only be proposed for a market that has closed. This "
        "one has not closed yet."
    )


class MarketPendingResolution(MarketError):
    """A write arrived for a market whose proposed outcome awaits a decision. [3.1] #9.

    Not `MarketClosed`: this market has a proposal to show instead of a
    propose control. ADR 0013.
    """

    status_code = 409
    code = "market_pending_resolution"
    message = (
        "An outcome has already been proposed for this market, and it is "
        "waiting on a second administrator."
    )


class MarketNotOpen(MarketError):
    """An early close was asked for on a draft or submitted market. [2.3] #7.

    Not `MarketClosed`, because the remedy is opposite: this market may still
    be published.
    """

    status_code = 409
    code = "market_not_open"
    message = (
        "Only a market that is open to traders can be closed. This one has "
        "not been published, so nothing can be traded in it."
    )


class MarketNotPendingResolution(MarketError):
    """An approval or rejection arrived for a market with no proposal. [3.2] #10.

    Every state but PENDING_RESOLUTION and APPROVED, including a proposal just
    rejected by a racing request. Not `MarketAlreadyApproved`: this market may
    still get a proposal. ADR 0016.
    """

    status_code = 409
    code = "market_not_pending_resolution"
    message = (
        "There is no outcome proposal waiting on this market, so there is "
        "nothing to approve or reject."
    )


class MarketAlreadyApproved(MarketError):
    """Any write or decision arrived for a market whose outcome is approved. [3.2] #10.

    Checked before who is asking, so the proposer is told it is approved.
    ADR 0016.
    """

    status_code = 409
    code = "market_already_approved"
    message = (
        "A second administrator has already approved this market's outcome. "
        "The proposal is final and the next step is settlement."
    )


class SecondAdministratorRequired(MarketError):
    """The administrator who proposed an outcome tried to approve or reject it. [3.2] #10.

    The two-person rule, decided on the account id. 403: this account will
    never be allowed to decide this proposal. ADR 0016.
    """

    status_code = 403
    code = "second_administrator_required"
    message = (
        "The administrator who proposed this outcome cannot approve or reject "
        "it. A different administrator has to decide."
    )


class ProposalSuperseded(MarketError):
    """A decision quoted a proposal that has since been replaced. [3.2] #10.

    The row lock cannot catch this: the reject-and-repropose cycle had already
    committed. 409: reload and review the proposal now waiting. ADR 0016.
    """

    status_code = 409
    code = "proposal_superseded"
    message = (
        "The proposal you reviewed has been replaced by a newer one. Reload the "
        "market and review the proposal that is waiting now."
    )


class MalformedCursor(MarketError):
    """[X-1] #104 - the `cursor` parameter did not come from a previous response.

    Refused rather than restarting from page one, which would loop a client
    forever. Same status, code and message as the audit, auth and ledger
    services'.
    """

    status_code = 400
    code = "malformed_cursor"
    message = "Pass back the `next_cursor` from the previous response, unmodified."
