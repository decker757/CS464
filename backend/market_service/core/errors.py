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

    The shape, not an error anything raises. Four actions refuse this way — a
    submission or publication whose terms do not pass ([1.1] #1, [1.3] #3), a
    proposal whose winner or evidence does not ([3.1] #9), and an early close
    or a rejection with no usable reason ([2.3] #7, [3.2] #10) — and every one
    of them is a form the administrator is looking at, so all of them want
    every offending field marked at once instead of one save at a time.

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


class CloseIncomplete(IncompleteError):
    """The reason given for closing a market early is not usable. [2.3] #7

    The third action to refuse by field, and the one that shows the base class
    paying for itself: `controller/errors.py` attaches `details` to anything
    that is an `IncompleteError`, so this envelope cost a code and a sentence.

    Its own code rather than a reuse of `draft_incomplete`, for the reason
    `ProposalIncomplete` gives. `reason` is not a term of the market and exists
    only on this request, so a form that mapped it onto the create form's
    inputs would find nothing to mark.
    """

    code = "close_incomplete"

    DEFAULT_MESSAGE = "This market cannot be closed without a reason."


class RejectionIncomplete(IncompleteError):
    """The reason given for rejecting a proposed outcome is not usable. [3.2] #10

    Its own code rather than a reuse of `close_incomplete`, although the field
    has the same name and today the same floor. The two modals are different
    forms answering different questions, and a frontend that branched on
    `close_incomplete` to repaint a rejection would be relying on a coincidence
    — the same one `MIN_REJECTION_REASON_LENGTH` declines to rely on by being
    its own constant.
    """

    code = "rejection_incomplete"

    DEFAULT_MESSAGE = "This proposal cannot be rejected without a reason."


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
    [2.3] #7 lets an administrator close a market out from under exactly that
    form.

    Also what an early close is refused with, in both of the ways a market can
    already have stopped: the sweep wrote CLOSED, or it has not run yet and
    `close_time` has passed anyway. One error for both is the point — the
    administrator gets the same answer either side of the sweep, which is what
    deriving the answer rather than reading the status column buys. ADR 0014.
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


class MarketNotOpen(MarketError):
    """An early close was asked for on a market traders cannot reach. [2.3] #7.

    A draft or a submitted market has never been in front of anybody. Nothing
    can be traded in it, so there is nothing for an early close to stop, and
    the administrator is looking at a control that should not have been
    offered.

    The other half of the same gate is MarketClosed, and they are kept distinct
    because the remedy is opposite: a market that is not open yet may still be
    published, and one that has closed never trades again.

    409 rather than 404 or 422: the market is real, the request is well formed,
    and it is the state that is wrong.
    """

    status_code = 409
    code = "market_not_open"
    message = (
        "Only a market that is open to traders can be closed. This one has "
        "not been published, so nothing can be traded in it."
    )


class MarketNotPendingResolution(MarketError):
    """An approval or rejection arrived for a market with no proposal. [3.2] #10.

    Covers every state that is not waiting on a decision and has not already
    had one: a draft, a submitted market, an open one, and a closed one nobody
    has proposed for — including one whose proposal has just been rejected,
    which is what a second rejection racing the first sees. One error for all
    of them, because the remedy is the same: there is nothing here to decide,
    so reload and stop offering the control.

    The opposite number of MarketPendingResolution, and kept apart from
    MarketAlreadyApproved for the reason MarketNotOpen is kept apart from
    MarketClosed. A market with no proposal may still get one; a market that has
    been approved is past deciding.
    """

    status_code = 409
    code = "market_not_pending_resolution"
    message = (
        "There is no outcome proposal waiting on this market, so there is "
        "nothing to approve or reject."
    )


class MarketAlreadyApproved(MarketError):
    """A write or a decision arrived for a market whose outcome is approved. [3.2] #10.

    One error for every caller, the way MarketAlreadyOpen is, because every one
    of them is the same situation. A second approval has nothing to add, a
    rejection would undo a decision a second administrator has already signed,
    a new proposal would replace the winner [3.4] #12 is about to pay out on, an
    early close has nothing left to stop, and a save must not touch the terms
    the approval agreed to. All of them should reload and drop the control.

    Checked before who is asking, so the proposer looking at a market somebody
    else has already approved is told it is approved rather than that they may
    not approve it. The first is the fact; the second is true and no longer
    matters. ADR 0016.
    """

    status_code = 409
    code = "market_already_approved"
    message = (
        "A second administrator has already approved this market's outcome. "
        "The proposal is final and the next step is settlement."
    )


class SecondAdministratorRequired(MarketError):
    """The administrator who proposed an outcome tried to decide it. [3.2] #10.

    The ticket's first acceptance criterion as a refusal, and the whole of the
    two-person rule: no single administrator controls a payout. Raised on a
    rejection as well as an approval, because a proposer rejecting their own
    proposal is a withdrawal with no second administrator in it — the path
    ADR 0013 declined to build as an un-propose.

    Decided on the account id, never the username. A username can be reissued
    after a deletion and changed by a rename; "a different administrator" is a
    claim about the account.

    403 rather than 409, by the distinction `NotAnAdministrator` draws: the
    session is fine and this account will never be allowed to decide this
    proposal, so retrying cannot help. It is not a state conflict — the market
    is in exactly the right state, for somebody else — and not a 404, because
    there is no draft here to hide. ADR 0016.
    """

    status_code = 403
    code = "second_administrator_required"
    message = (
        "The administrator who proposed this outcome cannot approve or reject "
        "it. A different administrator has to decide."
    )


class ProposalSuperseded(MarketError):
    """A decision quoted a proposal that is no longer the one waiting. [3.2] #10.

    The market is pending resolution and the caller may decide it, but not the
    proposal they read. Between opening the review and sending the decision,
    that proposal was rejected by somebody else and the creator proposed
    again. Applying the request anyway would approve a winner, or clear
    evidence, the administrator has never seen — and the row lock cannot catch
    it, because nothing was overlapping: the whole reject-and-repropose cycle
    had already committed.

    409, because the remedy is the usual one for a state conflict: reload, read
    the proposal that is actually waiting, and decide that one. Checked after
    who is asking, so a proposer holding a stale page is still told they may
    not decide at all, and before the reason, so a rejecter is not asked to
    improve a reason about the wrong proposal. ADR 0016.
    """

    status_code = 409
    code = "proposal_superseded"
    message = (
        "The proposal you reviewed has been replaced by a newer one. Reload the "
        "market and review the proposal that is waiting now."
    )
