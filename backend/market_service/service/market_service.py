"""A market's lifecycle: save, submit, publish, close early, propose, approve, reject.
[1.1] #1, [1.3] #3, [2.3] #7, [3.1] #9, [3.2] #10.

The automatic close is in `service/closing.py`, and what each audit entry
carries is in `service/market_audit.py`. Every function takes the caller as an
argument, so "only the creator can see this" is testable without a request.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.errors import (
    CloseIncomplete,
    DraftIncomplete,
    MarketAlreadyApproved,
    MarketAlreadyOpen,
    MarketAlreadySettled,
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
from model.entities import Market, MarketOutcome, MarketStatus, ResolutionSource
from model.schemas import (
    MarketCloseRequest,
    MarketDraftRequest,
    OutcomeApprovalRequest,
    OutcomeProposalRequest,
    OutcomeRejectionRequest,
)
from service import market_audit
from service.audit import Actor
from service.closing import is_open_for_trading
from service.validation import (
    problems_blocking_close,
    problems_blocking_proposal,
    problems_blocking_rejection,
    problems_blocking_submission,
)

# Statuses whose terms no save may write, and the error each one gives.
# ADR 0008, 0013, 0016. A table rather than `status not in (DRAFT, SUBMITTED)`
# because each status owes a different error. Do not reuse it to gate
# `close_early`, `propose_outcome` or the decisions: each of those *requires*
# one of these states, so "frozen" is the wrong question for them.
_FROZEN_STATUS_ERRORS: dict[MarketStatus, type[MarketError]] = {
    MarketStatus.OPEN: MarketAlreadyOpen,
    MarketStatus.CLOSED: MarketClosed,
    MarketStatus.PENDING_RESOLUTION: MarketPendingResolution,
    MarketStatus.APPROVED: MarketAlreadyApproved,
    MarketStatus.SETTLED: MarketAlreadySettled,
}

# Statuses in which an outcome is already named. Its own table because it is
# checked before the callers' other state gates; see `_refuse_if_resolving`.
_RESOLUTION_STATUS_ERRORS: dict[MarketStatus, type[MarketError]] = {
    MarketStatus.PENDING_RESOLUTION: MarketPendingResolution,
    MarketStatus.APPROVED: MarketAlreadyApproved,
    MarketStatus.SETTLED: MarketAlreadySettled,
}

# Same code and `details` as a refused submission; only the sentence differs.
# ADR 0008.
_PUBLISH_BLOCKED = (
    "This market cannot be published yet. Its terms no longer pass every rule."
)


def _refuse_if_frozen(market: Market) -> None:
    """Raise the status's error if this market's terms can no longer be written.

    Reads the market, never the request: an autosave, a resubmission and a
    second publish all get the same answer.
    """
    error = _FROZEN_STATUS_ERRORS.get(market.status)
    if error is not None:
        raise error


def _refuse_if_resolving(market: Market) -> None:
    """Raise if an outcome has already been proposed for this market, approved or settled.

    `close_early` and `propose_outcome` call this before their other state
    gates, which would otherwise answer `market_closed` or `market_not_closed`:
    true, and useless to an administrator who needs to see the proposal. The
    decisions do not call this; `_proposal_to_decide` gates them.
    """
    error = _RESOLUTION_STATUS_ERRORS.get(market.status)
    if error is not None:
        raise error


async def _load(
    session: AsyncSession,
    market_id: uuid.UUID,
    *,
    creator_id: uuid.UUID | None,
    for_update: bool,
) -> Market:
    """The query `get` and `get_any` share; raises `MarketNotFound`.

    `creator_id` has no default, so neither caller can widen its scope by
    leaving it out. ADR 0014.
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
    """The caller's market for this form, locked for the rest of the save; None if new.

    Locked because `_refuse_if_frozen` decides from this status. Unlocked, a
    resubmission racing a publish passes the check and writes SUBMITTED back
    over OPEN. Do not drop the lock to spare the autosave. A missing row locks
    nothing, so `save`'s retry still handles the insert race. ADR 0015.
    """
    stmt = (
        select(Market)
        .where(Market.creator_id == creator_id, Market.draft_key == draft_key)
        .with_for_update()
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _clear_children(session: AsyncSession, market: Market) -> None:
    """Delete the existing outcomes and sources, and flush that on its own.

    Do not fold this into the next flush: SQLAlchemy issues the new rows'
    INSERTs before the orphans' DELETEs, `uq_outcome_position` fires, and every
    autosave after the first returns 500. Only called for a loaded market.
    """
    market.outcomes.clear()
    market.resolution_sources.clear()
    await session.flush()


def _blank_to_none(value: str | None) -> str | None:
    """Strip a form field; blank becomes None, so readers handle one kind of absent."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _apply(
    market: Market, data: MarketDraftRequest, default_liquidity_b: Decimal
) -> None:
    """Overwrite the market's terms with what the form last held. ADR 0004."""
    market.question = _blank_to_none(data.question)
    market.description = _blank_to_none(data.description)
    market.close_time = data.close_time
    market.resolution_time = data.resolution_time
    market.resolution_criteria = _blank_to_none(data.resolution_criteria)

    # [1.2] #2. An omitted `b` takes the configured default, so a market is
    # always priceable; an explicit one overrides it on any save. The subsidy
    # has no default: no platform-wide answer fits every market.
    market.liquidity_b = (
        data.liquidity_b if data.liquidity_b is not None else default_liquidity_b
    )
    market.seed_subsidy = data.seed_subsidy

    # Replaced wholesale; `_clear_children` has already emptied them. Positions
    # follow the client's order, so the admin's arrangement survives a reload.
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


async def _database_now(session: AsyncSession) -> datetime:
    """Postgres's clock, the one the close sweep stamps `closed_at` with. #179.

    `clock_timestamp()`, never `func.now()`: that is the transaction's start,
    read before the caller queued for its lock. So call this after the lock.
    DECISIONS.md, "Every decision a market records is stamped with Postgres's
    `clock_timestamp()`".
    """
    return (await session.execute(select(func.clock_timestamp()))).scalar_one()


async def _save_once(
    session: AsyncSession,
    actor: Actor,
    data: MarketDraftRequest,
    now: datetime | None,
) -> tuple[Market, list[ValidationProblem], bool]:
    """One attempt at `save`. Raises IntegrityError if it lost the insert race."""
    market = await _find_by_draft_key(session, actor.id, data.draft_key)
    created = market is None

    # After the lock, for the reason `publish` gives.
    now = now or await _database_now(session)

    if market is None:
        market = Market(creator_id=actor.id, draft_key=data.draft_key)
        session.add(market)
    else:
        # Frozen terms refuse every save, whatever status is asked for, and
        # before the rule below. Do not remove: the form still open behind the
        # publish button would revert a live market to a draft. ADR 0008.
        _refuse_if_frozen(market)

        if market.status is MarketStatus.SUBMITTED and data.status is MarketStatus.DRAFT:
            # A late autosave must not silently un-submit a market. Changing
            # one means submitting again. ADR 0004.
            raise MarketNotEditable

        await _clear_children(session, market)

    # Read here rather than inside `_apply`, so `_apply` stays pure.
    _apply(market, data, get_settings().default_liquidity_b)

    problems = problems_blocking_submission(market, now=now)
    submitting = data.status is MarketStatus.SUBMITTED

    if submitting:
        if problems:
            # Nothing is committed: the session dependency rolls back as this
            # propagates, discarding this request's edits too. ADR 0004.
            raise DraftIncomplete(problems)
        market.status = MarketStatus.SUBMITTED
        market.submitted_at = now
    else:
        market.status = MarketStatus.DRAFT

    await session.flush()

    if submitting:
        # After the flush so a new market has its id; before the commit so the
        # submission and its entry are one write. ADR 0006.
        await market_audit.record_submission(session, actor, market, now)

    await session.commit()
    return market, problems, created


async def get(
    session: AsyncSession,
    creator_id: uuid.UUID,
    market_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> Market:
    """One of the caller's own markets; raises `MarketNotFound` otherwise.

    `creator_id` is in the WHERE clause rather than checked afterwards, so a
    draft cannot leak. `for_update` locks the market row only; pass it only
    when the read decides a write. ADR 0015.
    """
    return await _load(session, market_id, creator_id=creator_id, for_update=for_update)


async def get_any(
    session: AsyncSession, market_id: uuid.UUID, *, for_update: bool = False
) -> Market:
    """One market, whoever created it; raises `MarketNotFound`. [2.3] #7.

    The only unscoped read here, kept a separate function so an unscoped read
    is visible at the call site. Safe only for callers that refuse a draft
    before touching it (ADR 0014, ADR 0016). Do not point a read route at it:
    every admin would see every draft, and nothing would fail.
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


async def save(
    session: AsyncSession,
    actor: Actor,
    data: MarketDraftRequest,
    *,
    now: datetime | None = None,
) -> tuple[Market, list[ValidationProblem], bool]:
    """Create or update the caller's market for `data.draft_key`, and commit. ADR 0004.

    Returns the market, the problems that would block submission, and whether
    this call created it (201 versus 200). A submission that fails validation
    raises `DraftIncomplete` and writes nothing. Takes the whole `Actor`
    because a submission's audit entry names who acted, and this service
    cannot look a user up (ADR 0003).
    """
    try:
        return await _save_once(session, actor, data, now)
    except IntegrityError:
        # Two first saves for one form raced and the unique (creator_id,
        # draft_key) caught the loser. Retry rather than 500: the second pass
        # finds the winner's row and writes the loser's newer payload to it.
        await session.rollback()
        return await _save_once(session, actor, data, now)


async def publish(
    session: AsyncSession,
    actor: Actor,
    market_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> Market:
    """Move one of the caller's SUBMITTED markets to OPEN, and commit.

    [1.3] #3, ADR 0008. Locks the market row. Raises `MarketNotFound` for a
    market that is not the caller's, the frozen status's error or
    `MarketNotSubmitted` for the wrong state, and `DraftIncomplete` if the
    terms no longer pass against the clock now. Nothing about the terms
    travels with the request, and there is no unpublish.
    """
    # Creator-scoped like every read here (404, not 403), and locked so a
    # double-click cannot publish twice. ADR 0008, ADR 0015.
    market = await get(session, actor.id, market_id, for_update=True)

    # Before the SUBMITTED check, so an open or closed market is told what it
    # is rather than "submit it first".
    _refuse_if_frozen(market)
    if market.status is not MarketStatus.SUBMITTED:
        raise MarketNotSubmitted

    # Sampled after the lock, never on the way in. The wait for the lock is
    # unbounded, and a clock read before it could let a market whose close
    # time passed while it queued go live. The clock is a value a check
    # depends on, so ADR 0015's rule applies to it. Postgres's clock, so every
    # time a market records agrees with the sweep's `closed_at`. #179.
    now = now or await _database_now(session)

    problems = problems_blocking_submission(market, now=now)
    if problems:
        raise DraftIncomplete(problems, message=_PUBLISH_BLOCKED)

    market.status = MarketStatus.OPEN
    market.published_at = now

    # The action and its audit entry are one write. ADR 0006.
    await session.flush()
    await market_audit.record_publication(session, actor, market, now)
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
    """Stop an open market before its close time, with a reason, and commit.

    [2.3] #7, ADR 0014. Any administrator may close any market. Locks the
    market row. Raises the resolution errors, `MarketNotOpen` for a market
    never published, `MarketClosed` for one the clock has already stopped,
    and `CloseIncomplete` for an unusable reason. Moves only `status` and
    `closed_at`; `close_time` is kept, so a `closed_at` before it marks a
    market stopped by hand. There is no reopen.
    """
    # Locked: a double-clicked confirm must not log two closes. ADR 0015.
    market = await get_any(session, market_id, for_update=True)

    _refuse_if_resolving(market)

    # Split from the check below, which is also False for these, because the
    # remedy is the opposite: this market may still be published.
    if market.status in (MarketStatus.DRAFT, MarketStatus.SUBMITTED):
        raise MarketNotOpen

    # After the lock, for the reason `publish` gives.
    now = now or await _database_now(session)

    # Derived from the clock, not the status column, so the answer is
    # `market_closed` on either side of the sweep. ADR 0014.
    if not is_open_for_trading(market, now=now):
        raise MarketClosed

    problems = problems_blocking_close(request)
    if problems:
        raise CloseIncomplete(problems)

    market.status = MarketStatus.CLOSED
    market.closed_at = now

    # One write with its audit entry, which is the only copy of the reason.
    # ADR 0006, ADR 0014.
    await session.flush()
    await market_audit.record_early_close(
        session, actor, market, request.reason.strip(), now
    )
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
    """Name the winning outcome of one of the caller's closed markets, and commit.

    [3.1] #9, ADR 0013. Locks the market row. Raises the resolution errors,
    `MarketNotClosed`, and `ProposalIncomplete` for a winner or evidence that
    fails validation. Mints a fresh `proposal_id` for the decision to quote.

    Gates on the status column, not the clock: the opposite of `close_early`,
    on purpose, because here that is the stricter direction. Do not "fix" it
    to derive. ADR 0013.
    """
    # Locked: a double-click must not log two proposals. ADR 0015.
    market = await get(session, actor.id, market_id, for_update=True)

    _refuse_if_resolving(market)
    if market.status is not MarketStatus.CLOSED:
        raise MarketNotClosed

    # After the lock, for the reason `publish` gives. ADR 0015.
    now = now or await _database_now(session)

    problems = problems_blocking_proposal(market, proposal)
    if problems:
        raise ProposalIncomplete(problems)

    market.status = MarketStatus.PENDING_RESOLUTION
    # Fresh per proposal and never reused, so a decision can say which one it
    # read. ADR 0016.
    market.proposal_id = uuid.uuid4()
    market.proposed_outcome_id = proposal.winning_outcome_id
    market.proposed_by_id = actor.id
    # Snapshotted: this service cannot resolve an id to a name later. ADR 0013.
    market.proposed_by_username = actor.username
    market.proposed_at = now
    market.proposal_evidence_url = _blank_to_none(proposal.evidence_url)
    market.proposal_evidence_note = _blank_to_none(proposal.evidence_note)

    # One write with its audit entry. ADR 0006.
    await session.flush()
    await market_audit.record_proposal(session, actor, market, now)
    await session.commit()

    return market


# Below `get_any`, which it reads through, and above the two decisions it gates.
async def _proposal_to_decide(
    session: AsyncSession,
    actor: Actor,
    market_id: uuid.UUID,
    proposal_id: uuid.UUID | None,
) -> Market:
    """Lock the market a decision is about, or raise why it cannot be decided. ADR 0016.

    Shared by approve and reject so they cannot drift. The order is the
    contract: the unscoped row lock, then state (`MarketAlreadyApproved`,
    `MarketAlreadySettled`, `MarketNotPendingResolution`), then identity against `proposed_by_id` —
    never the username or `creator_id` (`SecondAdministratorRequired`), then
    the quoted `proposal_id` (`ProposalSuperseded`). A null id matches only a
    proposal made before ids existed.
    """
    market = await get_any(session, market_id, for_update=True)

    if market.status is MarketStatus.APPROVED:
        raise MarketAlreadyApproved
    if market.status is MarketStatus.SETTLED:
        raise MarketAlreadySettled
    if market.status is not MarketStatus.PENDING_RESOLUTION:
        raise MarketNotPendingResolution

    if market.proposed_by_id == actor.id:
        raise SecondAdministratorRequired

    if market.proposal_id != proposal_id:
        raise ProposalSuperseded

    return market


async def approve_outcome(
    session: AsyncSession,
    actor: Actor,
    market_id: uuid.UUID,
    approval: OutcomeApprovalRequest,
    *,
    now: datetime | None = None,
) -> Market:
    """A second administrator agrees with the pending proposal, and commits.

    [3.2] #10, ADR 0016. Gated, and the row locked, by `_proposal_to_decide`.
    Sets APPROVED and the approver's id, name and time; the proposal, the
    terms and `closed_at` stay as they were. There is no un-approve.
    """
    market = await _proposal_to_decide(
        session, actor, market_id, approval.proposal_id
    )

    # After the lock, for the reason `publish` gives. ADR 0015.
    now = now or await _database_now(session)

    market.status = MarketStatus.APPROVED
    market.approved_by_id = actor.id
    # Snapshotted, like `proposed_by_username`. ADR 0016.
    market.approved_by_username = actor.username
    market.approved_at = now

    # One write with its audit entry. ADR 0006.
    await session.flush()
    await market_audit.record_approval(session, actor, market, now)
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
    """A second administrator rejects the pending proposal, with a reason, and commits.

    [3.2] #10, ADR 0016. Gated, and the row locked, by `_proposal_to_decide`;
    then raises `RejectionIncomplete` for an unusable reason. Returns the
    market to CLOSED and clears all seven proposal columns; `closed_at` is
    untouched. The audit entry is the only record of what was rejected, by
    whom and why.
    """
    market = await _proposal_to_decide(
        session, actor, market_id, rejection.proposal_id
    )

    # After the lock, for the reason `publish` gives. ADR 0015.
    now = now or await _database_now(session)

    problems = problems_blocking_rejection(rejection)
    if problems:
        raise RejectionIncomplete(problems)

    # Before the clear: afterwards this is the only copy of the proposal.
    # ADR 0016.
    snapshot = market_audit.decision_snapshot(market)

    market.status = MarketStatus.CLOSED
    market.proposal_id = None
    market.proposed_outcome_id = None
    market.proposed_by_id = None
    market.proposed_by_username = None
    market.proposed_at = None
    market.proposal_evidence_url = None
    market.proposal_evidence_note = None

    await session.flush()
    await market_audit.record_rejection(
        session, actor, market, rejection.reason.strip(), snapshot, now
    )
    await session.commit()

    return market
