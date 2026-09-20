"""Business rules for a market's lifecycle. [1.1] #1, [1.3] #3, [2.3] #7, [3.1] #9, [3.2] #10.

Drafting and submitting, publishing, stopping a market early, proposing the
outcome once trading has stopped, and a second administrator approving or
rejecting that proposal. The *automatic* close is in `service/closing.py`,
because the clock performs it and no administrator does; `close_early` here is
the one an administrator performs, and it asks that file whether the market it
is about to stop is still running at all.

HTTP is not mentioned in this file; failures are raised as domain errors. Every
function takes the caller's id as an argument rather than reading it from
anywhere ambient, which is what makes "only the creator can see this" a rule
that can be tested without a request.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.errors import (
    CloseIncomplete,
    DraftIncomplete,
    MarketAlreadyApproved,
    MarketAlreadyOpen,
    MarketClosed,
    MarketError,
    MarketNotClosed,
    MarketNotEditable,
    MarketNotFound,
    MarketNotOpen,
    MarketNotPendingResolution,
    MarketNotSubmitted,
    MarketPendingResolution,
    ProposalIncomplete,
    ProposalSuperseded,
    RejectionIncomplete,
    SecondAdministratorRequired,
    ValidationProblem,
)
from model.audit import AdminAction
from model.entities import Market, MarketOutcome, MarketStatus, ResolutionSource
from model.schemas import (
    PRICING_DECIMAL_PLACES,
    MarketCloseRequest,
    MarketDraftRequest,
    OutcomeApprovalRequest,
    OutcomeProposalRequest,
    OutcomeRejectionRequest,
)
from service import audit
from service.audit import Actor
from service.closing import is_open_for_trading
from service.validation import (
    problems_blocking_close,
    problems_blocking_proposal,
    problems_blocking_rejection,
    problems_blocking_submission,
)

# The statuses from which a market's terms are final, and what a write to one
# is told. [1.3] #3 froze OPEN; [F-4] #44 added CLOSED; [3.1] #9 added
# PENDING_RESOLUTION, which is the table paying for itself — one row rather
# than a third branch in each of the two write paths — and [3.2] #10 added
# APPROVED the same way.
#
# A table rather than a branch per status, because there are two write paths
# and a branch per status means a branch in each of them. `core/errors.py`
# already names where this is going — "[1.4] #4 widens it into a general rule
# about non-draft markets" — and [3.4] #12's SETTLED is then one more row.
#
# It now happens to list every status except DRAFT and SUBMITTED, which makes
# a negative check look equivalent. It is not, twice over: each status owes the
# administrator a different error, and "frozen" is a fact about writes to the
# *terms* rather than about a market being finished.
#
# That second half is why none of `close_early`, `propose_outcome`,
# `approve_outcome` or `reject_outcome` below consults this table, though every
# one of them looks as though it should. CLOSED is in here — a save to a closed
# market is refused — and CLOSED is exactly the state a proposal requires,
# while OPEN is exactly the state an early close requires and
# PENDING_RESOLUTION exactly the state a decision requires. A shared lookup
# would have to answer four different questions from one row.
_FROZEN_STATUS_ERRORS: dict[MarketStatus, type[MarketError]] = {
    MarketStatus.OPEN: MarketAlreadyOpen,
    MarketStatus.CLOSED: MarketClosed,
    MarketStatus.PENDING_RESOLUTION: MarketPendingResolution,
    MarketStatus.APPROVED: MarketAlreadyApproved,
}


def _refuse_if_frozen(market: Market) -> None:
    """Raise if this market's terms can no longer be written to.

    Consults the market and never what the request asked for, because being
    finished is what decides this. An autosave from a form still open behind
    the publish button, a resubmission and a second publish are all the same
    answer, and the error says which kind of finished this market is.
    """
    error = _FROZEN_STATUS_ERRORS.get(market.status)
    if error is not None:
        raise error


# The statuses in which a market's outcome is already named, and what an action
# that needs one not to be is told. [3.1] #9 added PENDING_RESOLUTION, [3.2] #10
# added APPROVED, and [3.4] #12's SETTLED is one more row.
#
# A table for the reason `_FROZEN_STATUS_ERRORS` is one — two callers, and a
# branch per status would be a branch in each of them — and a table of its own
# rather than rows in that one, because of when it is checked.
_RESOLUTION_STATUS_ERRORS: dict[MarketStatus, type[MarketError]] = {
    MarketStatus.PENDING_RESOLUTION: MarketPendingResolution,
    MarketStatus.APPROVED: MarketAlreadyApproved,
}


def _refuse_if_resolving(market: Market) -> None:
    """Raise if an outcome has already been proposed for this market, or approved.

    Checked before every other state gate by both callers, `close_early` and
    `propose_outcome`, and that ordering is the whole reason this is not a row
    in `_FROZEN_STATUS_ERRORS`. A market in resolution has stopped trading *and*
    has a winner named on it, so every later gate in those functions would
    describe it in a way that is true and useless: `propose_outcome` would
    answer `market_not_closed`, telling the administrator to wait for a market
    that will never be CLOSED again, and `close_early` would answer
    `market_closed`, hiding the proposal. The remedy is to look at the outcome
    already named, which is what these errors say and no other one does.

    Each status keeps its own error because each takes a different remedy: a
    pending market is waiting on a second administrator, an approved one on
    nobody.

    [3.2] #10's approval and rejection do not call this. They want a market in
    PENDING_RESOLUTION rather than refusing it, and `_proposal_to_decide`
    refuses everything else itself.
    """
    error = _RESOLUTION_STATUS_ERRORS.get(market.status)
    if error is not None:
        raise error


async def save(
    session: AsyncSession,
    actor: Actor,
    data: MarketDraftRequest,
    *,
    now: datetime | None = None,
) -> tuple[Market, list[ValidationProblem], bool]:
    """Create or update the caller's market identified by `data.draft_key`.

    Takes the whole `Actor` rather than a bare creator id because a submission
    writes an audit entry, and that entry has to name who the administrator was
    at the time. This service cannot look a user id up in `auth.users`, so the
    username and role travel with the request or not at all. [4.3] #15.

    Returns the market, the problems that would block submission, and whether
    this call created it, which the controller turns into 201 versus 200.

    The three-second autosave calls this with status DRAFT, possibly hundreds
    of times for one market. That is why the lookup is by (creator_id,
    draft_key) rather than an insert: the browser has no id to send until the
    first response arrives, and it may never arrive.

    A submission that fails validation writes nothing at all. The edits that
    came with it are not lost, because the next autosave three seconds later
    saves them as a draft; a 422 that had also written half the change would be
    a worse contract than one that is simply atomic.
    """
    now = now or datetime.now(UTC)

    try:
        return await _save_once(session, actor, data, now)
    except IntegrityError:
        # Two autosaves for the same form were in flight at once — a slow
        # request still running when the timer fired again — and both saw no
        # existing row. The unique constraint on (creator_id, draft_key) is
        # what catches the loser.
        #
        # Retried rather than surfaced. The loser's payload is the newer one,
        # and the row the winner just created is exactly the row it wanted to
        # write to, so the second pass finds it and updates it. Letting the
        # IntegrityError out would 500 on a request that is entirely valid.
        await session.rollback()
        return await _save_once(session, actor, data, now)


async def _save_once(
    session: AsyncSession,
    actor: Actor,
    data: MarketDraftRequest,
    now: datetime,
) -> tuple[Market, list[ValidationProblem], bool]:
    """One attempt. Raises IntegrityError if it lost an insert race."""
    market = await _find_by_draft_key(session, actor.id, data.draft_key)
    created = market is None

    if market is None:
        market = Market(creator_id=actor.id, draft_key=data.draft_key)
        session.add(market)
    else:
        # [1.3] #3, [F-4] #44. Frozen terms take no save of any kind — not an
        # autosave from a form still open behind the publish button, and not a
        # resubmission. Traders are pricing against these terms, or have
        # finished doing so and [3.1] #9 is about to settle on them. Checked
        # before the rule below, because being finished outranks what the
        # request wanted to do.
        _refuse_if_frozen(market)

        if market.status is MarketStatus.SUBMITTED and data.status is MarketStatus.DRAFT:
            # An autosave arriving after the submit button was pressed. Letting
            # it through would silently revert a finished market to a draft,
            # and the admin would have no reason to suspect it. Changing a
            # submitted market means submitting it again, which re-runs every
            # rule.
            raise MarketNotEditable

        await _clear_children(session, market)

    # Read at the edge rather than inside `_apply`, so the rule that a
    # market falls back to the configured liquidity stays a pure function
    # of its arguments and can be tested without touching the environment.
    _apply(market, data, get_settings().default_liquidity_b)

    problems = problems_blocking_submission(market, now=now)
    submitting = data.status is MarketStatus.SUBMITTED

    if submitting:
        if problems:
            # Nothing is committed. The caller's session dependency rolls the
            # transaction back as this propagates, so the edits that arrived
            # with the rejected submission are discarded too — and so does the
            # audit entry below, which is never reached anyway.
            raise DraftIncomplete(problems)
        market.status = MarketStatus.SUBMITTED
        market.submitted_at = now
    else:
        market.status = MarketStatus.DRAFT

    await session.flush()

    if submitting:
        # After the flush, so a market created by this very call already has
        # the id the entry names. Before the commit, so the entry and the
        # submission are one atomic write: if the commit fails, there is no
        # orphan record of a submission that never happened, and if it
        # succeeds there is no submission with no record of who made it.
        # [4.3] #15.
        await _record_submission(session, actor, market, now)

    await session.commit()
    return market, problems, created


async def publish(
    session: AsyncSession,
    actor: Actor,
    market_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> Market:
    """Move one submitted market to OPEN, making it tradeable. [1.3] #3.

    Addressed by the market's own id rather than by `draft_key`, because this
    is not a save. Nothing about the terms travels with the request: publishing
    takes what was already submitted and nothing else, so there is no shape of
    request that can change a market and expose it in the same call. That is
    what makes the audit entry below a truthful record of what went live.

    Three things have to hold, and each fails differently on purpose:

    - the market is the caller's own, or it is a 404 like every other read
    - it is SUBMITTED — a draft is a 409 asking for the submit button first,
      and one already open is a 409 saying so
    - it still passes every rule in `service/validation.py`

    That last check is not belt-and-braces. A market is validated at submission
    against the clock at submission, and `close_time` must be in the future;
    leave a submitted market alone for long enough and that stops being true.
    Publishing it then would put a market in front of traders that closes in
    the past. Re-running the same pure function against the clock now is the
    whole of the ticket's first acceptance criterion, and ADR 0004 wrote
    `problems_blocking_submission` to be reused here rather than restated.

    One way. There is no unpublish, and `_save_once` refuses every later write.
    Exactly once, too: the row is locked for the status check, so two requests
    racing cannot both report having published the same market.

    That re-run is also why [F-4] #44's sweeper can never find a market that
    was born due. `close_time` has to be strictly in the future at this
    instant, so a market reaches OPEN with time left on the clock every time,
    and CLOSED is only ever reached by the clock actually running out.
    """
    # Reuses the read rule rather than restating it, so "not yours" is a 404
    # here for exactly the reason it is a 404 there: a 403 would confirm the
    # market exists. Publication stays with the creator even though ADR 0007
    # makes the admin tier flat, because every other route in this service is
    # already scoped that way and widening it is a decision for a ticket that
    # asks for it.
    #
    # Locked, because the check below and the write after it are one decision
    # and an admin double-clicking the publish button sends two requests. Read
    # unlocked, both would see SUBMITTED, both would flip the status, and the
    # log would carry two entries claiming to be the moment this market went
    # live. Under READ COMMITTED the loser blocks here and re-reads the row the
    # winner committed, so it sees OPEN and is refused.
    market = await get(session, actor.id, market_id, for_update=True)

    # Before the refusal below, so the message is true. A market that is open
    # or closed is not "still a draft, press submit first": an open one has
    # nothing left to do, and there is no sequence of actions that publishes a
    # closed one, because publishing re-runs the rule that a close time must be
    # in the future and that market's is in the past by definition.
    _refuse_if_frozen(market)
    if market.status is not MarketStatus.SUBMITTED:
        raise MarketNotSubmitted

    # Sampled *after* the lock, never on the way in. The wait above is
    # unbounded — a second publisher, a resubmission, anything else holding
    # this row — and a clock read before it decides using a time from before
    # the wait. The whole point of the re-validation below is that a market
    # which sat submitted past its own close time must not go live, and a
    # stale `now` is how it goes live anyway: the close time passes while this
    # request queues, and the check still sees the future. ADR 0015's rule
    # names the status, the balance and the idempotency key; the clock is the
    # same kind of thing, because it is a value a check depends on that can
    # change while the lock is being waited for.
    now = now or datetime.now(UTC)

    problems = problems_blocking_submission(market, now=now)
    if problems:
        raise DraftIncomplete(problems, message=_PUBLISH_BLOCKED)

    market.status = MarketStatus.OPEN
    market.published_at = now

    # Flushed before the entry and committed after it, for the same reason
    # submission is: the action and the record of it are one write. A crash
    # between them cannot leave a market visible to traders with nothing saying
    # who made it so. ADR 0006.
    await session.flush()
    await _record_publication(session, actor, market, now)
    await session.commit()

    return market


async def close_early(
    session: AsyncSession,
    actor: Actor,
    market_id: uuid.UUID,
    request: MarketCloseRequest,
    *,
    now: datetime | None = None,
) -> Market:
    """Stop one open market before its closing time, on a stated reason. [2.3] #7.

    The only way a market reaches CLOSED by anybody's decision. Every other
    close is the clock's, and the clock is not an actor: `close_due_markets`
    writes no audit entry, because there is nobody to name and the
    `market.published` entry already recorded the `close_time` that was
    approved. This one always writes an entry, because there is.

    Two things have to hold, and each fails differently on purpose:

    - the market is open to traders right now — a draft or a submitted one is a
      409 saying there is nothing to stop, one that has already stopped is a
      409 saying so, one awaiting resolution names the proposal instead, and
      one whose outcome is approved says that
    - the reason is usable, or it is a 422 naming the field

    And one thing deliberately does not: the market need not be the caller's.

    **Any administrator may close any market, and this was the first route in
    this service not scoped to the creator.** ADR 0007 made the tier flat,
    and a broken market that can only be stopped by an administrator who is
    asleep is not oversight. The 404-rather-than-403 rule everywhere else in
    this file protects *drafts*, which is [1.1] #1's requirement; this route
    only ever touches a market traders can already see, so there is nothing
    left for that rule to hide. What replaces it is the audit entry, which
    names who reached into somebody else's market and why. ADR 0014.

    **The gate derives rather than reading the status column**, which looks
    like the opposite of what `propose_outcome` does below, and is the same
    decision made twice. Both pick the direction whose failure is a control
    that appears or disappears a beat late rather than a wrong write. There it
    means reading the status: gating on the clock could accept a proposal on a
    market this service has not finished processing. Here it means the clock:
    for the few seconds between `close_time` passing and the sweep, the status
    still says `open`, and an early close accepted in that window would append
    an entry saying an administrator stopped trading that the clock had already
    stopped. `is_open_for_trading` is the same predicate the trade path uses,
    so this refuses exactly when a trade would, and `MarketClosed` is the
    answer either side of the sweep. ADR 0014.

    **`close_time` is not moved, and `closed_at` is what records this.** The
    closing time is a term the administrator published and traders read; the
    audit entry says it was cut short, and rewriting the row would make the log
    disagree with the market it describes. A market whose `closed_at` precedes
    its `close_time` is exactly a market that was stopped by hand.

    Nothing else is touched. Positions are the ledger's ([F-1] #41, ADR 0005)
    and this service holds none, so "positions retained" is true here by
    construction rather than by care: two columns on one row move, and nothing
    in this repository deletes a holding.

    One way, like `publish`. There is no reopen, `_save_once` refuses every
    later write, and the market's next move is [3.1] #9's proposal.
    """
    # Locked for the same reason `publish` and `propose_outcome` lock: the
    # check below and the write after it are one decision, and an administrator
    # double-clicking the confirm button in the modal sends two requests. Read
    # unlocked, both would see OPEN and the log would carry two entries closing
    # one market, with two different reasons.
    market = await get_any(session, market_id, for_update=True)

    _refuse_if_resolving(market)

    # A market traders never saw. Split out because the remedy is the opposite
    # of the one below — this market may still be published — and because
    # `is_open_for_trading` answers False for both and cannot tell them apart.
    if market.status in (MarketStatus.DRAFT, MarketStatus.SUBMITTED):
        raise MarketNotOpen

    # After the lock, for the reason `publish` gives at length. Here the cost
    # of a stale read is an audit entry claiming an administrator stopped
    # trading that the clock had already stopped — the exact entry ADR 0014
    # refuses to write, arrived at by waiting rather than by reading the
    # status column.
    now = now or datetime.now(UTC)

    # OPEN or CLOSED, and the clock decides which of those is true right now.
    # See the docstring: a market whose close time has passed is closed whether
    # or not the sweep has got to it, and gets the same error either way.
    if not is_open_for_trading(market, now=now):
        raise MarketClosed

    problems = problems_blocking_close(request)
    if problems:
        raise CloseIncomplete(problems)

    market.status = MarketStatus.CLOSED
    market.closed_at = now

    # Flushed before the entry and committed after it, exactly as submission,
    # publication and proposal are: the action and the record of it are one
    # write. It matters more here than anywhere else in this file, because the
    # entry is the *only* copy of the reason — a market that stopped early with
    # no entry is a market nobody can ever explain. ADR 0006.
    await session.flush()
    await _record_early_close(session, actor, market, request.reason.strip(), now)
    await session.commit()

    return market


async def propose_outcome(
    session: AsyncSession,
    actor: Actor,
    market_id: uuid.UUID,
    proposal: OutcomeProposalRequest,
    *,
    now: datetime | None = None,
) -> Market:
    """Name the winning outcome of one closed market, with evidence. [3.1] #9.

    The fourth transition, and the first one that carries a decision rather
    than only a moment. `publish` takes no body precisely so that it cannot
    change anything; this one has to, because the proposed winner and the
    evidence for it are new information that exists nowhere until an
    administrator supplies it. What it still cannot do is change the market's
    *terms* — nothing in `OutcomeProposalRequest` names one — so the market a
    second administrator is asked to approve in [3.2] #10 is the market traders
    actually traded.

    Four things have to hold, and each fails differently on purpose:

    - the market is the caller's own, or it is a 404 like every other read
    - it is not already awaiting a decision, or already decided — a 409 saying
      whose proposal is waiting, or that it has been approved, because a second
      proposal would silently replace the thing somebody is being asked to
      agree to, or has agreed to
    - it is CLOSED — a 409 asking the administrator to wait, because proposing
      an outcome for a market people can still trade announces the answer to a
      question they are still betting on
    - the winner is one of this market's outcomes, and there is evidence

    **The status is the gate, not the clock,** which is the one place this
    function deliberately reads a materialised value rather than deriving it.
    ADR 0011 decided that: `close_time` passing is what stops trading, and the
    sweeper writes CLOSED a few seconds later, so for those few seconds this
    returns `market_not_closed` for a market that is genuinely finished. That
    is the safe direction of the error — a propose control that appears a beat
    late, rather than a proposal on a live market — and the wait is bounded by
    `CLOSE_SWEEP_SECONDS`. It is also a beat nobody will notice: a market's
    `resolution_time` is necessarily later than its `close_time`, so an
    administrator with an answer to propose is not standing on the closing bell.

    Not one way, and that is the difference from `publish`. [3.2] #10's
    `reject_outcome` sends the market back to CLOSED and clears these columns,
    which is why the audit entry rather than the row is the durable record that
    a proposal was ever made — and a rejected market is proposed for again
    through this same function, by the same creator.
    """
    now = now or datetime.now(UTC)

    # Locked for the same reason `publish` locks: the status check below and
    # the write after it are one decision, and an administrator double-clicking
    # the propose button sends two requests. Read unlocked, both would see
    # CLOSED, the second would write its winner over the first, and the log
    # would carry two entries proposing different outcomes for one market.
    market = await get(session, actor.id, market_id, for_update=True)

    _refuse_if_resolving(market)
    if market.status is not MarketStatus.CLOSED:
        raise MarketNotClosed

    problems = problems_blocking_proposal(market, proposal)
    if problems:
        raise ProposalIncomplete(problems)

    market.status = MarketStatus.PENDING_RESOLUTION
    # New for every proposal, never reused, so a decision can say which one it
    # read. [3.2] #10 refuses an approval or rejection that quotes any other.
    market.proposal_id = uuid.uuid4()
    market.proposed_outcome_id = proposal.winning_outcome_id
    market.proposed_by_id = actor.id
    # Snapshotted, like the audit entry's actor and for the same reason: this
    # service cannot resolve an id against `auth.users`, so a name not stored
    # now is a name nobody can render later. ADR 0003.
    market.proposed_by_username = actor.username
    market.proposed_at = now
    market.proposal_evidence_url = _blank_to_none(proposal.evidence_url)
    market.proposal_evidence_note = _blank_to_none(proposal.evidence_note)

    # Flushed before the entry and committed after it, exactly as submission
    # and publication are: the action and the record of it are one write.
    await session.flush()
    await _record_proposal(session, actor, market, now)
    await session.commit()

    return market


async def approve_outcome(
    session: AsyncSession,
    actor: Actor,
    market_id: uuid.UUID,
    approval: OutcomeApprovalRequest,
    *,
    now: datetime | None = None,
) -> Market:
    """A second administrator agrees with a proposed outcome. [3.2] #10.

    The two-person rule the resolution epic is built around: no single
    administrator controls a payout. The proposer named a winner and gave
    evidence; this is somebody else reading that evidence and agreeing with
    it, and until it happens [3.4] #12 has nothing to settle.

    Three things have to hold, checked in this order and failing differently on
    purpose — `_proposal_to_decide` holds all three, for this and for
    `reject_outcome`:

    - there is a proposal waiting — an already-approved market is a 409 saying
      so, and any other state is a 409 saying there is nothing to decide
    - the caller is not the administrator who proposed it, or it is a 403
    - it is the proposal the caller read, or it is a 409 `proposal_superseded`

    **The body names a proposal and says nothing about it.** Everything an
    approval agrees to is already on the row, so the approver supplies no
    winner, no evidence and no note — a note would be free text only
    `audit_svc` could ever read, and would blur the log's rule that a non-null
    `reason` means an administrator had to explain themselves. What the market
    id cannot say is *which* proposal: one can be rejected and replaced while
    the approver is still reading it. `approval.proposal_id` says it. ADR 0016.

    **Any administrator but the proposer may approve**, including one who did
    not create the market — which is every approver there will ever be, because
    `propose_outcome` is scoped to the creator and the approver must be someone
    else. So this reads through `get_any`, like `close_early`, and for a
    different reason: there the tier is flat, here the rule demands somebody
    other than the author.

    **APPROVED is a status, and only four columns move.** The status and the
    approver's id, name and time. The proposal stays exactly as it was —
    `proposed_outcome_id` is still the winner [3.4] #12 settles against — and
    so do the terms and `closed_at`. The row then carries both identities,
    which is the ticket's third acceptance criterion.

    One way. There is no un-approve here; [3.3] #11's dispute window is where
    an approved outcome can still be challenged.
    """
    now = now or datetime.now(UTC)

    market = await _proposal_to_decide(
        session, actor, market_id, approval.proposal_id
    )

    market.status = MarketStatus.APPROVED
    market.approved_by_id = actor.id
    # Snapshotted, like `proposed_by_username` and for the same reason: this
    # service cannot resolve an id against `auth.users`. ADR 0003.
    market.approved_by_username = actor.username
    market.approved_at = now

    # Flushed before the entry and committed after it, like every other
    # decision in this file: the approval and the record of who gave it are one
    # write. ADR 0006.
    await session.flush()
    await _record_approval(session, actor, market, now)
    await session.commit()

    return market


async def reject_outcome(
    session: AsyncSession,
    actor: Actor,
    market_id: uuid.UUID,
    rejection: OutcomeRejectionRequest,
    *,
    now: datetime | None = None,
) -> Market:
    """A second administrator sends a proposed outcome back, with a reason. [3.2] #10.

    The ticket's second acceptance criterion, and the one transition in this
    service that moves a market backwards: PENDING_RESOLUTION to CLOSED. The
    market is then exactly what it was before anybody proposed — the creator
    may propose again, with a different winner or better evidence, through
    `propose_outcome`.

    The same gates as `approve_outcome`, through the same function and in the
    same order, and then one more: the reason has to be usable, or it is a 422
    naming the field. State and identity come first, so a proposer who types
    "x" is told they may not decide at all rather than to write a longer
    reason for a decision they may not make.

    **The proposer may not reject their own proposal either.** ADR 0013 built
    no un-propose, because a self-service withdrawal would be a second path
    back to CLOSED with no second administrator in it. A proposer rejecting
    their own proposal is that path by another name.

    **All seven proposal columns are cleared; `closed_at` is not touched.** The
    row holds no proposal afterwards, so a later one starts from nothing and a
    reader never sees half of a rejected proposal beside half of a new one.
    `closed_at` says when trading stopped, and a rejection does not change
    that. The approver columns are null already, by the invariant on them.

    **Who rejected it, why, and what was rejected live in the audit log.** No
    `rejected_by_*` columns: once the proposal is gone from the row there is
    nothing on it for a rejecter's name to describe. The entry carries the
    reason in `reason` and the whole proposal in `context`, taken before the
    columns are cleared — `audit_svc` holds no grant on this table, so a fact
    not copied onto the entry is a fact nobody can recover. ADR 0016.
    """
    now = now or datetime.now(UTC)

    market = await _proposal_to_decide(
        session, actor, market_id, rejection.proposal_id
    )

    problems = problems_blocking_rejection(rejection)
    if problems:
        raise RejectionIncomplete(problems)

    # Before the clear below, and that ordering is the whole of this line. The
    # columns are about to be nulled, and after that this snapshot is the only
    # description of the proposal being rejected anywhere in the system.
    snapshot = _decision_snapshot(market)

    market.status = MarketStatus.CLOSED
    market.proposal_id = None
    market.proposed_outcome_id = None
    market.proposed_by_id = None
    market.proposed_by_username = None
    market.proposed_at = None
    market.proposal_evidence_url = None
    market.proposal_evidence_note = None

    await session.flush()
    await _record_rejection(
        session, actor, market, rejection.reason.strip(), snapshot, now
    )
    await session.commit()

    return market


async def _proposal_to_decide(
    session: AsyncSession,
    actor: Actor,
    market_id: uuid.UUID,
    proposal_id: uuid.UUID | None,
) -> Market:
    """The market an approval or a rejection is about, locked, or why not. [3.2] #10.

    One function for both decisions so the two cannot drift apart on the part
    that matters most: which markets may be decided, and by whom. The order is
    the contract.

    1. **Locked, and unscoped.** `get_any` rather than `get`, because the
       decider is never the creator (see `approve_outcome`). Locked because
       the checks below and the caller's write are one decision, and three
       races end on this row: a double-clicked approve, two administrators
       rejecting at once, and one approving while another rejects. Under READ
       COMMITTED the loser blocks here and re-reads the row the winner
       committed, so it sees APPROVED or CLOSED and is refused below. ADR 0015.
    2. **State.** An approved market is `market_already_approved` — checked
       first, so the proposer looking at a market somebody else has approved
       is told the fact that matters. Anything else that is not
       PENDING_RESOLUTION has no proposal to decide.
    3. **Identity.** The caller's id against `proposed_by_id`, never the
       username, which can be reissued. Not against `creator_id`: the proposer
       is the creator today, but the rule this ticket enforces is about who
       proposed. That is the whole of the two-person rule.
    4. **Which proposal.** The `proposal_id` the caller quotes against the one
       on the row, or `proposal_superseded`. This is the check the lock cannot
       make: a lock catches two decisions that overlap, and this catches one
       that arrives after a whole reject-and-repropose cycle has committed
       beneath it, which would otherwise approve — or clear, with a reason
       written about something else — a proposal the caller never read. After
       identity, so a proposer on a stale page is still told they may not
       decide at all. A null matches only a proposal made before proposal ids
       existed, which is pending with a null; one made since has an id, so a
       stale null is refused like any other stale id.

    A draft's existence is disclosed to another administrator here as a 409
    rather than hidden behind a 404, exactly as `close_early` discloses it. It
    is the same trade ADR 0014 made, and it is narrow: the 409 says a market
    with that id exists and is not awaiting a decision, and says nothing about
    what it contains.
    """
    market = await get_any(session, market_id, for_update=True)

    if market.status is MarketStatus.APPROVED:
        raise MarketAlreadyApproved
    if market.status is not MarketStatus.PENDING_RESOLUTION:
        raise MarketNotPendingResolution

    if market.proposed_by_id == actor.id:
        raise SecondAdministratorRequired

    if market.proposal_id != proposal_id:
        raise ProposalSuperseded

    return market


async def get(
    session: AsyncSession,
    creator_id: uuid.UUID,
    market_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> Market:
    """One of the caller's own markets.

    `creator_id` is part of the query rather than a check after the fact. A
    filter cannot be forgotten the way an `if` can, and an unfiltered read
    followed by a permission check is how a draft leaks.

    `for_update` takes a row lock, and only the status transitions ask for one.
    Reads must not: a lock on every GET would serialise the list page behind
    whatever happened to be writing. The lock covers the parent row alone — the
    selectin loaders fetch the children unlocked — which is all that is needed,
    because the only thing being serialised is a status transition on this row.
    """
    return await _load(session, market_id, creator_id=creator_id, for_update=for_update)


async def get_any(
    session: AsyncSession, market_id: uuid.UUID, *, for_update: bool = False
) -> Market:
    """One market, whoever created it. [2.3] #7.

    The only read in this service that is not scoped to the caller, and a
    second function rather than a `creator_id=None` on `get` above precisely
    because of that: an unscoped read is a decision, so it should be visible at
    the call site and greppable from here.

    Safe for its three callers and for a reason that does not generalise.
    `close_early` acts only on a market that is open to traders, and
    `approve_outcome` and `reject_outcome` only on one with a proposal waiting —
    markets every trader has already seen — and every one of them refuses
    anything else before it reads or writes a single term. What leaks is that
    a market with that id exists, as a 409 rather than a 404, and nothing about
    its contents: the trade ADR 0014 made, and ADR 0016 made again. Point a
    read route at this instead of `get` and [1.1] #1's rule that a draft is
    invisible to everyone but its creator is gone — silently, and with no test
    failing that does not already exist.
    """
    return await _load(session, market_id, creator_id=None, for_update=for_update)


async def list_for_creator(session: AsyncSession, creator_id: uuid.UUID) -> list[Market]:
    """Every market the caller owns, most recently touched first."""
    stmt = (
        select(Market)
        .where(Market.creator_id == creator_id)
        .order_by(Market.updated_at.desc())
    )
    return list((await session.execute(stmt)).scalars())


async def _record(
    session: AsyncSession,
    actor: Actor,
    market: Market,
    action: AdminAction,
    now: datetime,
    *,
    context: dict[str, object],
    reason: str | None = None,
) -> None:
    """Append one audit entry about a market.

    Holds how a market is named in the log — `target_type`, the id, and the
    question as the label — so that every action against a market names it the
    same way and a reader can scan one column.

    What varies is `context`, and only four shapes exist: the terms
    (`_record_terms`), the proposal (`_record_proposal`), the closing time an
    early close cut short (`_record_early_close`), and the proposal a second
    administrator decided (`_record_approval`, `_record_rejection`).

    `reason` is the free-text justification an administrator gave, and two
    actions here have one — [2.3] #7's early close and [3.2] #10's rejection;
    [4.2] #14's suspension will be the next, in the auth service. It is
    deliberately not where evidence goes: a URL and a note supporting a
    proposed outcome travel together in `context` alongside the outcome they
    support, because splitting a single body of evidence across two columns
    would make it unreadable. A reason explains a decision; evidence supports a
    claim. Defaulted to None so the four actions that have nothing to explain
    record nothing, rather than an empty string that a reader would have to
    tell apart from a null.
    """
    await audit.record(
        session,
        actor=actor,
        action=action,
        target_type="market",
        target_id=market.id,
        target_label=market.question,
        reason=reason,
        context=context,
        now=now,
    )


async def _record_terms(
    session: AsyncSession,
    actor: Actor,
    market: Market,
    action: AdminAction,
    now: datetime,
) -> None:
    """Append an entry carrying the market's terms.

    Submission and publication both come through here, and carry the identical
    shape deliberately, which is why there is one function rather than two.
    They are compared against each other more than either is read alone — the
    question the log is actually asked is whether anything changed between an
    administrator agreeing to some terms and those terms going live — and two
    builders are two chances for the answer to be "they are described
    differently".
    """
    await _record(session, actor, market, action, now, context=_terms_snapshot(market))


async def _record_submission(
    session: AsyncSession, actor: Actor, market: Market, now: datetime
) -> None:
    """Append the audit entry for a market leaving DRAFT. [4.3] #15.

    Submission is logged; autosave is not. An autosave fires every three
    seconds while the form is open, so recording it would bury every real
    decision under thousands of keystroke entries. Submission is the moment the
    administrator committed to a set of terms, and it is the moment worth being
    able to point at later.
    """
    await _record_terms(session, actor, market, AdminAction.MARKET_SUBMITTED, now)


async def _record_publication(
    session: AsyncSession, actor: Actor, market: Market, now: datetime
) -> None:
    """Append the audit entry for a market becoming tradeable. [1.3] #3.

    The ticket's third acceptance criterion, and the entry that matters most in
    this file. A submission is an administrator agreeing with themselves about
    some terms; a publication is the moment those terms were put in front of
    people who will commit credits to them. If one entry in this log is ever
    read in anger, it is this one.

    Carries the same snapshot as the submission rather than pointing back at
    it, and the duplication is the point. [1.4] #4 lets a submitted market be
    edited by submitting it again, so the terms that went live are not
    necessarily the terms of any one earlier entry, and a reader should not have
    to replay the whole history of a market to find out which set traders
    actually saw.
    """
    await _record_terms(session, actor, market, AdminAction.MARKET_PUBLISHED, now)


async def _record_early_close(
    session: AsyncSession, actor: Actor, market: Market, reason: str, now: datetime
) -> None:
    """Append the audit entry for a market stopped by hand. [2.3] #7.

    The whole of the ticket's third acceptance criterion, and the only place
    the reason is kept anywhere: nothing on `market.markets` holds it, so this
    entry is the record in the strong sense — lose it and a market that stopped
    early has no explanation left in the system.

    The `close_time` rides along in `context` because it is what makes the word
    "early" mean anything — the entry says when this market stopped, and only
    that value says what it stopped short of — and because `audit_svc` holds no
    grant on `market.markets` (ADR 0006), so a fact not carried on the entry is
    a fact the one role that may read this log cannot recover. The terms do
    not, for the reason `_record_proposal` gives: a published market is frozen,
    so the `market.published` entry already carries them and nothing has
    changed them since.
    """
    await _record(
        session,
        actor,
        market,
        AdminAction.MARKET_CLOSED_EARLY,
        now,
        reason=reason,
        # One key, and no snapshot function for it, unlike the two below. When
        # this market was going to close is the only fact a reader needs that
        # is not already on the entry — `occurred_at` is when it actually
        # closed, to the same value as `market.closed_at`.
        context={"close_time": _iso_or_none(market.close_time)},
    )


async def _record_proposal(
    session: AsyncSession, actor: Actor, market: Market, now: datetime
) -> None:
    """Append the audit entry for an outcome being proposed. [3.1] #9.

    The entry that makes "so the decision is documented" true, and the only
    durable half of it. The market row carries the same facts right now, but
    [3.2] #10's rejection clears those columns and returns the market to
    CLOSED — after which this entry is the only record that the proposal was
    ever made, by whom, and on what evidence.

    Carries the proposal rather than the terms, because the terms have not
    moved and cannot: a closed market is frozen, so `market.markets` still
    holds exactly the question, criteria and outcomes this proposal was made
    against. That is the one thing `_terms_snapshot` exists to defend against
    and it does not apply here — [1.4] #4 can rewrite a *submitted* market, so
    an entry about one has to carry its own copy; nothing can rewrite a closed
    one.
    """
    await _record(
        session,
        actor,
        market,
        AdminAction.MARKET_OUTCOME_PROPOSED,
        now,
        context=_proposal_snapshot(market),
    )


async def _record_approval(
    session: AsyncSession, actor: Actor, market: Market, now: datetime
) -> None:
    """Append the audit entry for a proposal a second administrator agreed to. [3.2] #10.

    Under the approver. The proposer's own `market.outcome_proposed` entry
    already names them, so the log shows the two-person rule as two entries by
    two actors about one market — which is how a reader checks it was kept
    without trusting the column that says so.

    Carries who proposed and when, as well as what, because this entry should
    answer "what exactly was agreed, and with whom" on its own. The proposal
    columns are still on the market today, but `audit_svc` cannot read them,
    and the entry must not depend on them staying there.
    """
    await _record(
        session,
        actor,
        market,
        AdminAction.MARKET_OUTCOME_APPROVED,
        now,
        context=_decision_snapshot(market),
    )


async def _record_rejection(
    session: AsyncSession,
    actor: Actor,
    market: Market,
    reason: str,
    snapshot: dict[str, object],
    now: datetime,
) -> None:
    """Append the audit entry for a proposal sent back. [3.2] #10.

    Takes the snapshot as an argument rather than building it, unlike every
    other recorder in this file, and that is not an inconsistency. By the time
    this runs `reject_outcome` has cleared the proposal from the market, so a
    snapshot built here would describe nothing; the caller took it while there
    was still something to describe.

    With this entry and the proposer's, the rejected proposal is reconstructable
    from the log alone — what was proposed, by whom, when, on what evidence, who
    rejected it and why — which is what lets the market row forget it.
    """
    await _record(
        session,
        actor,
        market,
        AdminAction.MARKET_OUTCOME_REJECTED,
        now,
        reason=reason,
        context=snapshot,
    )


def _proposal_snapshot(market: Market) -> dict[str, object]:
    """Which outcome was proposed, and what was offered in support of it.

    Led by `proposal_id`, so that an approval or a rejection entry — both of
    which carry this snapshot — names exactly which proposal entry it decided,
    even for a market proposed for twice.

    The label as well as the id, because the id is a join key for a table no
    reader of this log holds a grant on: `audit_svc` may read the log and
    nothing else, so an entry naming only a UUID would be unreadable to the one
    role that can read it at all. The label is what an administrator
    recognises; the id is what [3.4] #12 will settle against.

    The `None` fallback is unreachable — `_winner_problems` has already refused
    any winner that is not one of these outcomes — and is kept deliberately,
    because of where this function sits. It builds the audit entry that commits
    with the proposal, so a bare `next()` raising here would abort a valid
    admin action over a label lookup. A degraded log entry is recoverable; a
    refused proposal that should have succeeded is the failure ADR 0006 is
    trying to make impossible.
    """
    winner = next(
        (o for o in market.outcomes if o.id == market.proposed_outcome_id), None
    )
    return {
        # Null, not the string "None", for a proposal made before ids existed:
        # a reviewer quotes this value back, and "None" is not a UUID.
        "proposal_id": (
            str(market.proposal_id) if market.proposal_id is not None else None
        ),
        "winning_outcome_id": str(market.proposed_outcome_id),
        "winning_outcome": winner.label if winner is not None else None,
        "evidence_url": market.proposal_evidence_url,
        "evidence_note": market.proposal_evidence_note,
    }


def _decision_snapshot(market: Market) -> dict[str, object]:
    """The proposal a second administrator decided, and whose it was. [3.2] #10.

    `_proposal_snapshot` plus the three facts a proposal entry does not need to
    repeat — its own actor and `occurred_at` already say who and when — and a
    decision entry does, because its actor is somebody else. One builder for
    both the approval and the rejection, so the two entries have identical
    keys and "what was decided about this proposal" reads the same way
    whichever way it went.
    """
    return {
        **_proposal_snapshot(market),
        "proposed_by_id": str(market.proposed_by_id),
        "proposed_by_username": market.proposed_by_username,
        "proposed_at": _iso_or_none(market.proposed_at),
    }


def _terms_snapshot(market: Market) -> dict[str, object]:
    """What this market said, at the moment of an action worth recording.

    Written into `context` rather than left to a later read of `market.markets`,
    because [1.4] #4 will allow a submitted market to be edited by submitting it
    again. Without the snapshot the log would say that a market was submitted,
    or published, and the table would only ever show its latest terms — so the
    one question the log exists to answer, what did they actually approve, would
    need a row that no longer exists.

    Shared by both entries above so the two cannot describe one market in two
    shapes, which is what would make them hard to compare in the one case that
    matters: seeing whether anything changed between submission and going live.
    """
    return {
        # Stringified rather than floated. Nothing reads these to do arithmetic
        # with, unlike MarketOut's wire format, and the subsidy is money: a
        # record of what was approved should say 250.0000 and not something
        # that once rounded to it.
        "liquidity_b": _decimal_or_none(market.liquidity_b),
        "seed_subsidy": _decimal_or_none(market.seed_subsidy),
        "outcomes": [outcome.label for outcome in market.outcomes],
        "close_time": _iso_or_none(market.close_time),
        "resolution_time": _iso_or_none(market.resolution_time),
        # The rule that decides who wins, not only where to look it up. Every
        # field `problems_blocking_submission` insists on is a term, and this
        # is the one whose change between submission and going live would
        # matter most to a trader — and the one that was, for a while, the
        # only such field this snapshot left out. `description` is prose and
        # not a rule, and stays out.
        "resolution_criteria": market.resolution_criteria,
        "resolution_sources": [source.url for source in market.resolution_sources],
    }


# Said instead of "cannot be submitted yet" when the same rules refuse a
# publish. The error code and its `details` list are identical either way,
# because the offending fields are the same fields and a frontend that renders
# a refused submission should not need a second branch to render this.
_PUBLISH_BLOCKED = (
    "This market cannot be published yet. Its terms no longer pass every rule."
)

# The scale the pricing columns hold, borrowed from the request schema so one
# change to Numeric(18, 4) moves both.
_PRICING_QUANTUM = Decimal(1).scaleb(-PRICING_DECIMAL_PLACES)


def _iso_or_none(value: datetime | None) -> str | None:
    """A timestamp as ISO 8601, or null, for an audit entry's `context`.

    Extracted for the reason `_decimal_or_none` below was: two entries
    describing one value should describe it identically. Three copies of this
    ternary would be three chances for one of them to start truncating, or to
    emit an empty string where the others emit a null, and a reader comparing
    two entries would have to work out which.
    """
    return value.isoformat() if value is not None else None


def _decimal_or_none(value: Decimal | None) -> str | None:
    """Stringify at the column's scale, so two entries for one value match.

    A market created by this very call still holds the Decimal the request
    carried, while one loaded from Postgres holds the value as Numeric(18, 4)
    returned it. Left alone, the same 250 credits would be logged as "250" on
    the first submission and "250.0000" on the next, and a reader comparing two
    entries would have to work out whether anything had actually changed.

    Never rounds: model/schemas.py already refuses anything with more decimal
    places than the column can hold, so this only ever pads.
    """
    return str(value.quantize(_PRICING_QUANTUM)) if value is not None else None


async def _load(
    session: AsyncSession,
    market_id: uuid.UUID,
    *,
    creator_id: uuid.UUID | None,
    for_update: bool,
) -> Market:
    """The query the two reads above share, with the scope as an argument.

    `creator_id` is keyword-only and has no default, so neither caller can end
    up with the wider scope by leaving it out — which is the failure the split
    into two public functions exists to prevent, and it would be a shame to
    reintroduce it here. When it is given it goes into the WHERE clause, which
    is the whole of `get`'s promise.
    """
    stmt = select(Market).where(Market.id == market_id)
    if creator_id is not None:
        stmt = stmt.where(Market.creator_id == creator_id)
    if for_update:
        stmt = stmt.with_for_update()

    market = (await session.execute(stmt)).scalar_one_or_none()
    if market is None:
        raise MarketNotFound
    return market


async def _find_by_draft_key(
    session: AsyncSession, creator_id: uuid.UUID, draft_key: uuid.UUID
) -> Market | None:
    """The caller's market for this form, locked for the rest of the save.

    Locked, because `_refuse_if_frozen` decides from the status this returns
    whether the save may write at all, and `publish` holds this same row while
    it changes that status. Read unlocked, a resubmission racing a publish sees
    SUBMITTED, passes the check, blocks on its own INSERTs until the
    publication commits, and then lands anyway: it overwrites the terms traders
    are already pricing against, and writes SUBMITTED back over OPEN. The lock
    in `publish` cannot prevent that on its own, because a lock only serialises
    against other lockers. Under READ COMMITTED the loser now blocks here
    instead and re-reads the row the winner committed, so it sees OPEN and is
    refused the same way a second publish is.

    Every autosave pays for this, and that is fine. It is one row, held for
    one short transaction, and it is the lock `publish`, `close_early`,
    `propose_outcome`, `approve_outcome` and `reject_outcome` already take. Two
    autosaves for one form now queue rather than interleave, which is what
    last-write-wins meant anyway.

    A row that does not exist locks nothing, so the insert race handled in
    `save` is unchanged: two first saves both find nothing, the unique
    constraint catches the loser, and its retry finds the winner's row.
    """
    stmt = (
        select(Market)
        .where(Market.creator_id == creator_id, Market.draft_key == draft_key)
        .with_for_update()
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _clear_children(session: AsyncSession, market: Market) -> None:
    """Delete the existing outcomes and sources, and flush that on its own.

    Load-bearing rather than tidiness. Within a single flush SQLAlchemy issues
    the INSERTs for the replacement rows before the DELETEs for the orphans, so
    the new position 0 meets the old position 0 still sitting in the table and
    uq_outcome_position fires: every autosave after the first would 500.

    Only ever called for a market that was loaded from the database, so both
    collections are already populated by the selectin loader and nothing here
    goes back for them. A newly created market has no rows to collide with and
    skips this entirely, which also keeps the assignment in `_apply` off a
    freshly flushed object whose collections would have to be loaded to be
    replaced.
    """
    market.outcomes.clear()
    market.resolution_sources.clear()
    await session.flush()


def _apply(
    market: Market, data: MarketDraftRequest, default_liquidity_b: Decimal
) -> None:
    """Overwrite the market's terms with what the form last held.

    Last-write-wins on the whole document, because that is what an autosave of
    a single open form actually is. It is also the limitation: two browser tabs
    editing one draft_key will overwrite each other with no warning, which is
    recorded in ADR 0004 rather than solved, since a draft has one author by
    definition.
    """
    market.question = _blank_to_none(data.question)
    market.description = _blank_to_none(data.description)
    market.close_time = data.close_time
    market.resolution_time = data.resolution_time
    market.resolution_criteria = _blank_to_none(data.resolution_criteria)

    # [1.2] #2. An omitted `b` takes the configured default rather than staying
    # null, so a market is always priceable and the form can show a max loss
    # from the very first save. An explicit value overrides it, including on a
    # later save: the admin can raise `b`, see the larger worst case, and put
    # it back. The subsidy has no default — there is no sensible platform-wide
    # answer to how much this particular market is worth underwriting.
    market.liquidity_b = (
        data.liquidity_b if data.liquidity_b is not None else default_liquidity_b
    )
    market.seed_subsidy = data.seed_subsidy

    # Replaced wholesale rather than diffed; `_clear_children` has already
    # emptied and flushed whatever was there.
    #
    # Position comes from the order the client sent, so the arrangement the
    # admin made survives a reload, and [1.2] #2 and epic 3 have a stable
    # handle on an outcome that does not depend on its editable label.
    market.outcomes = [
        MarketOutcome(position=index, label=(item.label or "").strip())
        for index, item in enumerate(data.outcomes)
    ]
    market.resolution_sources = [
        ResolutionSource(
            position=index,
            url=(item.url or "").strip(),
            label=_blank_to_none(item.label),
        )
        for index, item in enumerate(data.resolution_sources)
    ]


def _blank_to_none(value: str | None) -> str | None:
    """A cleared form field should read as absent, not as an empty string.

    Otherwise "" and null both mean "not filled in" and every reader has to
    handle both.
    """
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
